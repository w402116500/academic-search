"""研究会话和研究运行的持久化服务。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models.collection import CollectionBibliographyEntry, ResearchCollection
from app.infra.db.models.document import Document, DocumentChunk, IngestionRun
from app.infra.db.models.paper import Paper
from app.infra.db.models.research import Conversation, Message, ResearchEvidence, ResearchRun
from app.modules.research.contracts import (
    RESEARCH_RUN_STAGE_DISPLAYS,
    AskResearchQuestionResponse,
    ConversationDetailResponse,
    ConversationResponse,
    ConversationStatus,
    CreateConversationRequest,
    ResearchError,
    ResearchErrorCode,
    ResearchEvidenceResponse,
    ResearchMessageResponse,
    ResearchRunMode,
    ResearchRunResponse,
    ResearchRunStage,
    ResearchRunStatus,
)
from app.modules.research.question_mode import (
    RESEARCH_QUESTION_MODE_CONFIG_KEY,
    research_question_mode_from_config,
)
from app.modules.research.queue import ResearchJobQueue, ResearchQueueError
from app.modules.research.settings import ResearchSettings, get_research_settings

_SERVER_ONLY_RETRIEVAL_TRACE_KEYS = frozenset({"failure_diagnostics", "presentation_quality"})


def _public_retrieval_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Project persisted run diagnostics into the ordinary API-safe trace."""
    return {
        key: value for key, value in trace.items() if key not in _SERVER_ONLY_RETRIEVAL_TRACE_KEYS
    }


class SqlAlchemyResearchConversationAdapter:
    """保证研究问题只能进入用户拥有、已完成索引的集合。"""

    def __init__(
        self,
        session: AsyncSession,
        queue: ResearchJobQueue | None = None,
        settings: ResearchSettings | None = None,
    ) -> None:
        """请求路径注入队列，纯读取操作可不创建 Redis 依赖。"""
        self._session = session
        self._queue = queue
        self._settings = settings

    async def create_conversation(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        request: CreateConversationRequest,
    ) -> ConversationResponse:
        """为可研究集合创建空会话，标题可延迟到第一条问题生成。"""
        collection = await self._require_owned_collection(
            owner_user_id=owner_user_id, collection_id=collection_id, lock=True
        )
        await self._require_researchable_documents(collection.id)
        conversation = Conversation(
            id=uuid4(),
            collection_id=collection.id,
            owner_user_id=owner_user_id,
            title=request.title,
            status=ConversationStatus.ACTIVE.value,
        )
        self._session.add(conversation)
        await self._session.commit()
        return self._conversation_response(conversation, message_count=0)

    async def list_conversations(
        self, *, owner_user_id: UUID, collection_id: UUID
    ) -> list[ConversationResponse]:
        """按最近活动顺序读取当前集合的非删除会话。"""
        await self._require_owned_collection(
            owner_user_id=owner_user_id, collection_id=collection_id, lock=False
        )
        rows = await self._session.execute(
            select(Conversation, func.count(Message.id))
            .outerjoin(Message, Message.conversation_id == Conversation.id)
            .where(
                Conversation.collection_id == collection_id,
                Conversation.owner_user_id == owner_user_id,
                Conversation.status != ConversationStatus.DELETED.value,
            )
            .group_by(Conversation.id)
            .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
        )
        return [
            self._conversation_response(conversation, message_count=message_count)
            for conversation, message_count in rows
        ]

    async def get_conversation(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        conversation_id: UUID,
    ) -> ConversationDetailResponse:
        """读取会话消息和运行快照；证据会在后续检索阶段填充。"""
        conversation = await self._require_owned_conversation(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            conversation_id=conversation_id,
            lock=False,
        )
        messages = list(
            await self._session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at, Message.id)
            )
        )
        runs = list(
            await self._session.scalars(
                select(ResearchRun)
                .where(ResearchRun.conversation_id == conversation.id)
                .order_by(ResearchRun.created_at, ResearchRun.id)
            )
        )
        run_by_message_id = {run.input_message_id: run.id for run in runs}
        return ConversationDetailResponse(
            conversation=self._conversation_response(conversation, message_count=len(messages)),
            messages=[
                self._message_response(message, run_by_message_id.get(message.id))
                for message in messages
            ],
            runs=[await self._run_response_with_evidences(run) for run in runs],
        )

    async def ask_question(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        conversation_id: UUID,
        content: str,
        model_config: dict[str, Any],
    ) -> AskResearchQuestionResponse:
        """先原子保存用户问题和 queued 运行，提交后才向 arq 投递。"""
        self._queue_or_raise()
        conversation = await self._require_owned_conversation(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            conversation_id=conversation_id,
            lock=True,
        )
        await self._require_researchable_documents(collection_id)
        quota = await self._assert_submission_quota(owner_user_id)
        requested_mode = research_question_mode_from_config(model_config)
        stored_model_config = {
            **model_config,
            RESEARCH_QUESTION_MODE_CONFIG_KEY: requested_mode.value,
        }

        now = datetime.now(UTC)
        user_message = Message(
            id=uuid4(),
            conversation_id=conversation.id,
            role="user",
            content=content,
            status="completed",
            metadata_json={},
        )
        run = ResearchRun(
            id=uuid4(),
            conversation_id=conversation.id,
            collection_id=collection_id,
            input_message_id=user_message.id,
            mode=ResearchRunMode.SINGLE_RAG.value,
            status=ResearchRunStatus.QUEUED.value,
            stage=ResearchRunStage.DISPATCH.value,
            langgraph_thread_id=f"research-{uuid4()}",
            model_config=stored_model_config,
            retrieval_trace={
                "stage": ResearchRunStage.DISPATCH.value,
                "rewrite_attempts": 0,
                "requested_mode": requested_mode.value,
                "governance": {"submission_quota": quota},
            },
        )
        # 新会话默认截取首个问题，用户提供的标题始终优先保留。
        if conversation.title is None:
            conversation.title = content[:80]
        conversation.updated_at = now
        self._session.add_all([user_message, run])
        await self._session.commit()

        try:
            job_id = await self._queue_or_raise().enqueue_research(run.id)
        except ResearchQueueError:
            await self._mark_queue_failed(run.id)
            raise ResearchError(
                ResearchErrorCode.QUEUE_UNAVAILABLE,
                "研究对话任务无法投递，请稍后重新提交问题。",
            ) from None

        run.arq_job_id = job_id
        await self._session.commit()
        return AskResearchQuestionResponse(
            user_message=self._message_response(user_message, run.id),
            research_run=self._run_response(run),
        )

    async def get_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        conversation_id: UUID,
        research_run_id: UUID,
    ) -> ResearchRunResponse:
        """读取属于当前会话的单个运行，不能借 UUID 跨集合探测状态。"""
        run = await self._require_owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            conversation_id=conversation_id,
            research_run_id=research_run_id,
            lock=False,
        )
        return await self._run_response_with_evidences(run)

    async def retry_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        conversation_id: UUID,
        research_run_id: UUID,
    ) -> ResearchRunResponse:
        """失败运行在同一业务记录内重试，保留 ID 供 LangGraph 恢复和前端轮询。"""
        self._queue_or_raise()
        run = await self._require_owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            conversation_id=conversation_id,
            research_run_id=research_run_id,
            lock=True,
        )
        if run.status != ResearchRunStatus.FAILED.value:
            raise ResearchError(
                ResearchErrorCode.RUN_NOT_RETRYABLE,
                "只有失败的研究运行可以重新投递。",
            )

        run.status = ResearchRunStatus.QUEUED.value
        run.stage = ResearchRunStage.DISPATCH.value
        run.error_code = None
        run.error_message = None
        run.cancel_requested_at = None
        run.finished_at = None
        run.stage_started_at = None
        run.arq_job_id = None
        requested_mode = research_question_mode_from_config(run.model_config)
        run.retrieval_trace = {
            "stage": ResearchRunStage.DISPATCH.value,
            "rewrite_attempts": 0,
            "requested_mode": requested_mode.value,
        }
        run.model_config = {
            **run.model_config,
            RESEARCH_QUESTION_MODE_CONFIG_KEY: requested_mode.value,
        }
        await self._session.commit()
        try:
            run.arq_job_id = await self._queue_or_raise().enqueue_research(run.id, retry=True)
        except ResearchQueueError:
            await self._mark_queue_failed(run.id)
            raise ResearchError(
                ResearchErrorCode.QUEUE_UNAVAILABLE,
                "研究对话任务无法重新投递，请稍后重试。",
            ) from None
        await self._session.commit()
        return self._run_response(run)

    async def cancel_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        conversation_id: UUID,
        research_run_id: UUID,
    ) -> ResearchRunResponse:
        """queued 运行立即取消；running 运行持久化协作停止请求并等待安全边界确认。"""
        run = await self._require_owned_run(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            conversation_id=conversation_id,
            research_run_id=research_run_id,
            lock=True,
        )
        now = datetime.now(UTC)
        if run.status == ResearchRunStatus.QUEUED.value:
            run.status = ResearchRunStatus.CANCELLED.value
            run.stage = ResearchRunStage.CANCELLED.value
            run.cancel_requested_at = now
            run.finished_at = now
            await self._session.commit()
            return self._run_response(run)
        if run.status == ResearchRunStatus.RUNNING.value:
            if run.cancel_requested_at is None:
                run.cancel_requested_at = now
                run.retrieval_trace = {
                    **run.retrieval_trace,
                    "cancellation": {
                        "state": "requested",
                        "requested_at": now.isoformat(),
                        "stage": run.stage,
                    },
                }
                await self._session.commit()
            return self._run_response(run)
        raise ResearchError(
            ResearchErrorCode.RUN_NOT_CANCELLABLE,
            "研究任务已经结束，不能再取消。",
        )

    async def delete_conversation(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        conversation_id: UUID,
    ) -> ConversationResponse:
        """软删除会话，保留已生成的研究审计记录直到后续保留策略处理。"""
        conversation = await self._require_owned_conversation(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            conversation_id=conversation_id,
            lock=True,
        )
        conversation.status = ConversationStatus.DELETED.value
        conversation.updated_at = datetime.now(UTC)
        await self._session.commit()
        return self._conversation_response(conversation, message_count=0)

    async def _mark_queue_failed(self, research_run_id: UUID) -> None:
        """队列故障必须持久化为可读失败，不允许 queued 状态永久悬挂。"""
        run = await self._session.scalar(
            select(ResearchRun).where(ResearchRun.id == research_run_id).with_for_update()
        )
        if run is None or run.status != ResearchRunStatus.QUEUED.value:
            return
        run.status = ResearchRunStatus.FAILED.value
        run.stage = ResearchRunStage.FAILED.value
        run.error_code = ResearchErrorCode.QUEUE_UNAVAILABLE.value
        run.error_message = "研究对话任务无法投递，请稍后重试。"
        run.finished_at = datetime.now(UTC)
        await self._session.commit()

    async def _assert_submission_quota(self, owner_user_id: UUID) -> dict[str, object]:
        """按 UTC 自然日检查真实已提交运行数，拒绝原因可写入新运行审计 trace。"""
        settings = self._settings or get_research_settings()
        now = datetime.now(UTC)
        period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        user_run_count = int(
            await self._session.scalar(
                select(func.count(ResearchRun.id))
                .join(ResearchCollection, ResearchCollection.id == ResearchRun.collection_id)
                .where(
                    ResearchCollection.owner_user_id == owner_user_id,
                    ResearchRun.created_at >= period_start,
                )
            )
            or 0
        )
        if user_run_count >= settings.rag_user_daily_research_run_limit:
            raise ResearchError(
                ResearchErrorCode.USER_QUOTA_EXCEEDED,
                "今日研究问题额度已用尽，请明天继续或联系管理员调整额度。",
            )
        global_run_count = int(
            await self._session.scalar(
                select(func.count(ResearchRun.id)).where(ResearchRun.created_at >= period_start)
            )
            or 0
        )
        if global_run_count >= settings.rag_global_daily_research_run_limit:
            raise ResearchError(
                ResearchErrorCode.GLOBAL_BUDGET_EXHAUSTED,
                "今日全局研究运行预算已用尽，请稍后再试。",
            )
        return {
            "period_start": period_start.isoformat(),
            "user_runs_used": user_run_count + 1,
            "user_run_limit": settings.rag_user_daily_research_run_limit,
            "global_runs_used": global_run_count + 1,
            "global_run_limit": settings.rag_global_daily_research_run_limit,
        }

    async def _require_owned_collection(
        self, *, owner_user_id: UUID, collection_id: UUID, lock: bool
    ) -> ResearchCollection:
        """校验集合归属和活动状态，统一隐藏其他用户的集合存在性。"""
        statement = select(ResearchCollection).where(
            ResearchCollection.id == collection_id,
            ResearchCollection.owner_user_id == owner_user_id,
            ResearchCollection.status == "active",
        )
        if lock:
            statement = statement.with_for_update()
        collection = await self._session.scalar(statement)
        if collection is None:
            raise ResearchError(
                ResearchErrorCode.COLLECTION_NOT_FOUND,
                "研究集合不存在、已归档或不属于当前用户。",
            )
        return collection

    async def _require_researchable_documents(self, collection_id: UUID) -> None:
        """只有 current 且 completed 的文档版本才允许创建或运行研究会话。"""
        count = await self._session.scalar(
            select(func.count(Document.id.distinct()))
            .select_from(Document)
            .join(IngestionRun, IngestionRun.document_id == Document.id)
            .join(
                CollectionBibliographyEntry,
                and_(
                    CollectionBibliographyEntry.collection_id == Document.collection_id,
                    CollectionBibliographyEntry.id == Document.bibliography_entry_id,
                ),
            )
            .where(
                Document.collection_id == collection_id,
                CollectionBibliographyEntry.status == "active",
                IngestionRun.status == "completed",
                IngestionRun.is_current.is_(True),
            )
        )
        if not count:
            raise ResearchError(
                ResearchErrorCode.NO_RESEARCHABLE_DOCUMENTS,
                "当前研究集合还没有完成索引的全文文献，暂时不能开始证据研究。",
            )

    async def _require_owned_conversation(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        conversation_id: UUID,
        lock: bool,
    ) -> Conversation:
        """确认会话与用户、集合完全一致，阻断跨工作区会话 ID 复用。"""
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.collection_id == collection_id,
            Conversation.owner_user_id == owner_user_id,
            Conversation.status == ConversationStatus.ACTIVE.value,
        )
        if lock:
            statement = statement.with_for_update()
        conversation = await self._session.scalar(statement)
        if conversation is None:
            raise ResearchError(
                ResearchErrorCode.CONVERSATION_NOT_FOUND,
                "研究会话不存在、已删除或不属于当前用户。",
            )
        return conversation

    async def _require_owned_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        conversation_id: UUID,
        research_run_id: UUID,
        lock: bool,
    ) -> ResearchRun:
        """按完整资源路径读取研究运行，防止通过运行 UUID 横向访问审计信息。"""
        statement = (
            select(ResearchRun)
            .join(Conversation, Conversation.id == ResearchRun.conversation_id)
            .where(
                ResearchRun.id == research_run_id,
                ResearchRun.collection_id == collection_id,
                ResearchRun.conversation_id == conversation_id,
                Conversation.owner_user_id == owner_user_id,
                Conversation.status != ConversationStatus.DELETED.value,
            )
        )
        if lock:
            statement = statement.with_for_update(of=ResearchRun)
        run = await self._session.scalar(statement)
        if run is None:
            raise ResearchError(ResearchErrorCode.RUN_NOT_FOUND, "当前会话中不存在该研究运行。")
        return run

    @staticmethod
    def _conversation_response(
        conversation: Conversation, *, message_count: int
    ) -> ConversationResponse:
        """避免将 ORM 关系懒加载暴露到 API 序列化阶段。"""
        return ConversationResponse(
            id=conversation.id,
            collection_id=conversation.collection_id,
            title=conversation.title,
            status=ConversationStatus(conversation.status),
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            message_count=message_count,
        )

    @staticmethod
    def _message_response(
        message: Message, research_run_id: UUID | None
    ) -> ResearchMessageResponse:
        """消息元数据仅用于展示；原文证据永远从独立证据表读取。"""
        return ResearchMessageResponse(
            id=message.id,
            conversation_id=message.conversation_id,
            role=message.role,
            content=message.content,
            status=message.status,
            metadata=dict(message.metadata_json),
            created_at=message.created_at,
            research_run_id=research_run_id,
        )

    @staticmethod
    def _run_response(run: ResearchRun) -> ResearchRunResponse:
        """将数据库稳定值和中文展示元数据一并返回给前端。"""
        stage = ResearchRunStage(run.stage)
        return ResearchRunResponse(
            id=run.id,
            conversation_id=run.conversation_id,
            collection_id=run.collection_id,
            input_message_id=run.input_message_id,
            output_message_id=run.output_message_id,
            arq_job_id=run.arq_job_id,
            mode=ResearchRunMode(run.mode),
            status=ResearchRunStatus(run.status),
            stage=stage,
            stage_display=RESEARCH_RUN_STAGE_DISPLAYS[stage],
            model_snapshot=dict(run.model_config),
            retrieval_trace=_public_retrieval_trace(run.retrieval_trace),
            error_code=run.error_code,
            error_message=run.error_message,
            cancel_requested_at=run.cancel_requested_at,
            started_at=run.started_at,
            stage_started_at=run.stage_started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
        )

    async def _run_response_with_evidences(self, run: ResearchRun) -> ResearchRunResponse:
        """为详情和运行轮询读取最终引用，RRF 中间候选不直接展示为回答证据。"""
        response = self._run_response(run)
        rows = await self._session.execute(
            select(ResearchEvidence, Document, CollectionBibliographyEntry, Paper)
            .join(DocumentChunk, DocumentChunk.id == ResearchEvidence.chunk_id)
            .join(IngestionRun, IngestionRun.id == DocumentChunk.ingestion_run_id)
            .join(Document, Document.id == IngestionRun.document_id)
            .join(
                CollectionBibliographyEntry,
                and_(
                    CollectionBibliographyEntry.collection_id == Document.collection_id,
                    CollectionBibliographyEntry.id == Document.bibliography_entry_id,
                ),
            )
            .outerjoin(Paper, Paper.id == CollectionBibliographyEntry.paper_id)
            .where(
                ResearchEvidence.research_run_id == run.id,
                ResearchEvidence.selection_stage == "final_citation",
                ResearchEvidence.is_cited.is_(True),
            )
            .order_by(ResearchEvidence.rank, ResearchEvidence.created_at)
        )
        evidences = [
            ResearchEvidenceResponse(
                id=evidence.id,
                chunk_id=evidence.chunk_id,
                display_index=evidence.rank,
                evidence_ref=(
                    str(evidence.locator_snapshot.get("evidence_ref"))
                    if isinstance(evidence.locator_snapshot, dict)
                    and evidence.locator_snapshot.get("evidence_ref") is not None
                    else None
                ),
                selection_stage=evidence.selection_stage,
                rank=evidence.rank,
                vector_score=evidence.vector_score,
                rrf_score=evidence.rrf_score,
                rerank_score=evidence.rerank_score,
                is_cited=evidence.is_cited,
                citation_excerpt=evidence.citation_excerpt,
                locator_snapshot=evidence.locator_snapshot,
                paper_id=paper.id if paper is not None else entry.paper_id,
                title=paper.title if paper is not None else entry.candidate_title,
                authors=paper.authors if paper is not None else entry.candidate_authors,
                publication_year=(
                    paper.publication_year
                    if paper is not None
                    else entry.candidate_publication_year
                ),
                source_url=document.source_url
                or (paper.official_url if paper is not None else entry.candidate_source_url),
            )
            for evidence, document, entry, paper in rows
        ]
        return response.model_copy(update={"evidences": evidences})

    def _queue_or_raise(self) -> ResearchJobQueue:
        """写路径必须有可用投递器，防止保存无人消费的 queued 运行。"""
        if self._queue is None:
            raise RuntimeError("提交研究问题时必须提供研究任务队列。")
        return self._queue
