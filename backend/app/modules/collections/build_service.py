"""待确认研究文献的集合构建、移出和入库重试服务。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.db.models.collection import CollectionPaper, ResearchCollection
from app.db.models.document import Document, IngestionRun
from app.db.models.paper import Paper
from app.modules.collections.build_contracts import (
    CollectionBuildError,
    CollectionBuildErrorCode,
    CollectionBuildResponse,
    CollectionBuildRunResponse,
    CollectionDocumentRemovalResponse,
    CollectionDocumentResponse,
    CollectionDocumentsResponse,
    CollectionIngestionSummary,
    IngestionRunResponse,
    IngestionRunStatus,
)
from app.modules.ingestion.job_queue import IngestionJobQueue, IngestionQueueError
from app.modules.ingestion.settings import IngestionSettings, get_ingestion_settings
from app.modules.workflow.state import WorkspaceWorkflowStage
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession


class ResearchCollectionBuildService:
    """维护集合构建的持久状态，不在 API 请求内执行 PDF 解析和向量化。"""

    def __init__(
        self,
        session: AsyncSession,
        queue: IngestionJobQueue | None = None,
        *,
        settings: IngestionSettings | None = None,
    ) -> None:
        """注入请求或 Worker 范围内的会话，队列在只读列表操作中可以省略。"""
        self._session = session
        self._queue = queue
        self._settings = settings

    async def list_documents(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
    ) -> CollectionDocumentsResponse:
        """读取活动文献、其最新运行和当前可问答数量。"""
        await self._require_owned_collection(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            lock=False,
        )
        rows = await self._session.execute(
            select(CollectionPaper, Paper, Document, IngestionRun)
            .join(Paper, Paper.id == CollectionPaper.paper_id)
            .join(
                Document,
                and_(
                    Document.collection_id == CollectionPaper.collection_id,
                    Document.paper_id == CollectionPaper.paper_id,
                ),
            )
            .outerjoin(IngestionRun, IngestionRun.document_id == Document.id)
            .where(
                CollectionPaper.collection_id == collection_id,
                CollectionPaper.status == "active",
            )
            # 同一文献可能有历史运行；第一个运行就是供页面展示的最新版本。
            .order_by(Document.created_at.desc(), IngestionRun.created_at.desc().nulls_last())
        )

        documents: list[CollectionDocumentResponse] = []
        seen_document_ids: set[UUID] = set()
        researchable_document_ids: set[UUID] = set()
        status_counts: dict[IngestionRunStatus, int] = {}

        for row in rows:
            collection_paper, paper, document, run = row._tuple()
            if run is not None:
                run_status = IngestionRunStatus(run.status)
                if run.is_current and run_status is IngestionRunStatus.COMPLETED:
                    researchable_document_ids.add(document.id)

            if document.id in seen_document_ids:
                continue
            seen_document_ids.add(document.id)

            latest_run = IngestionRunResponse.model_validate(run) if run is not None else None
            if latest_run is not None:
                status_counts[latest_run.status] = status_counts.get(latest_run.status, 0) + 1

            documents.append(
                CollectionDocumentResponse(
                    document_id=document.id,
                    paper_id=paper.id,
                    doi=paper.doi,
                    title=paper.title,
                    authors=paper.authors,
                    publication_year=paper.publication_year,
                    venue=paper.venue,
                    citation_text=paper.citation_text,
                    tags=collection_paper.tags,
                    note=collection_paper.note,
                    original_filename=document.original_filename,
                    byte_size=document.byte_size,
                    source_url=document.source_url,
                    access_rights=document.access_rights,
                    added_at=collection_paper.added_at,
                    latest_ingestion_run=latest_run,
                )
            )

        return CollectionDocumentsResponse(
            collection_id=collection_id,
            documents=documents,
            summary=CollectionIngestionSummary(
                active_document_count=len(documents),
                researchable_document_count=len(researchable_document_ids),
                ingestion_status_counts=status_counts,
            ),
        )

    async def build(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
    ) -> CollectionBuildResponse:
        """确认构建所有 pending 文献，再逐篇投递 Worker 以隔离队列失败。"""
        self._queue_or_raise()
        collection = await self._require_owned_collection(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            lock=True,
        )
        pending_runs = await self._pending_runs_for_update(collection_id)
        if not pending_runs:
            raise CollectionBuildError(
                CollectionBuildErrorCode.NO_PENDING_DOCUMENTS,
                "当前研究集合没有待确认构建的全文文献。",
            )
        await self._assert_submission_quota(
            owner_user_id=owner_user_id,
            requested_run_count=len(pending_runs),
        )

        # 先在同一数据库提交中打开 Worker 领取资格，避免已投递任务读到旧的 pending 状态。
        collection.workflow_stage = WorkspaceWorkflowStage.COLLECTION_BUILDING.value
        submitted_at = datetime.now(UTC)
        for run in pending_runs:
            run.status = IngestionRunStatus.QUEUED.value
            run.error_code = None
            run.error_message = None
            run.finished_at = None
            run.is_current = False
            run.submitted_at = submitted_at
        await self._session.commit()

        results = [await self._enqueue_run(run.id) for run in pending_runs]
        collection = await self._refresh_collection_stage(collection_id)
        return CollectionBuildResponse(
            collection_id=collection_id,
            workflow_stage=collection.workflow_stage,
            runs=results,
        )

    async def retry_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        ingestion_run_id: UUID,
    ) -> CollectionBuildResponse:
        """为失败运行创建新版本，保留旧运行的报错和审计信息。"""
        self._queue_or_raise()
        collection, previous = await self._owned_failed_run_for_update(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            ingestion_run_id=ingestion_run_id,
        )
        await self._assert_submission_quota(owner_user_id=owner_user_id, requested_run_count=1)
        new_run = IngestionRun(
            # 任务在提交后立即要投递，提前生成 UUID 可避免依赖 ORM flush 时机。
            id=uuid4(),
            document_id=previous.document_id,
            pipeline_version=previous.pipeline_version,
            status=IngestionRunStatus.QUEUED.value,
            stage="parse",
            # 配置在失败重试时继承，保证同一 pipeline 版本的复现信息不丢失。
            chunking_config=dict(previous.chunking_config),
            embedding_config=dict(previous.embedding_config),
            statistics={},
            attempt_no=previous.attempt_no + 1,
            is_current=False,
            submitted_at=datetime.now(UTC),
        )
        collection.workflow_stage = WorkspaceWorkflowStage.COLLECTION_BUILDING.value
        self._session.add(new_run)
        await self._session.commit()

        result = await self._enqueue_run(new_run.id)
        collection = await self._refresh_collection_stage(collection_id)
        return CollectionBuildResponse(
            collection_id=collection_id,
            workflow_stage=collection.workflow_stage,
            runs=[result],
        )

    async def remove_pending_document(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        document_id: UUID,
    ) -> CollectionDocumentRemovalResponse:
        """归档未构建文献而不删除正式 PDF，避免跨存储原子删除问题。"""
        await self._require_owned_collection(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            lock=True,
        )
        record = await self._session.execute(
            select(CollectionPaper, Document)
            .join(
                Document,
                and_(
                    Document.collection_id == CollectionPaper.collection_id,
                    Document.paper_id == CollectionPaper.paper_id,
                ),
            )
            .where(
                CollectionPaper.collection_id == collection_id,
                CollectionPaper.status == "active",
                Document.id == document_id,
            )
            .with_for_update(of=(CollectionPaper, Document))
        )
        row = record.one_or_none()
        if row is None:
            raise CollectionBuildError(
                CollectionBuildErrorCode.DOCUMENT_NOT_FOUND,
                "待确认集合中不存在该文献。",
            )
        collection_paper, document = row._tuple()
        run = await self._session.scalar(
            select(IngestionRun)
            .where(IngestionRun.document_id == document.id)
            .order_by(IngestionRun.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        if run is None or run.status != IngestionRunStatus.PENDING.value:
            raise CollectionBuildError(
                CollectionBuildErrorCode.DOCUMENT_NOT_PENDING,
                "只有尚未确认构建的文献可以从集合中移出。",
            )

        collection_paper.status = "archived"
        run.status = IngestionRunStatus.CANCELLED.value
        run.is_current = False
        run.finished_at = datetime.now(UTC)
        await self._session.commit()
        return CollectionDocumentRemovalResponse(
            document_id=document.id,
            collection_paper_status=collection_paper.status,
            ingestion_run_status=IngestionRunStatus(run.status),
        )

    async def refresh_collection_stage_for_ingestion_run(self, ingestion_run_id: UUID) -> None:
        """由 Worker 在成功或失败后调用，使工作区阶段反映真实可问答状态。"""
        collection = await self._session.scalar(
            select(ResearchCollection)
            .join(Document, Document.collection_id == ResearchCollection.id)
            .join(IngestionRun, IngestionRun.document_id == Document.id)
            .where(IngestionRun.id == ingestion_run_id)
            .with_for_update()
        )
        if collection is None or collection.status != "active":
            return
        await self._refresh_collection_stage_locked(collection)
        await self._session.commit()

    async def _enqueue_run(self, ingestion_run_id: UUID) -> CollectionBuildRunResponse:
        """投递一条已 queued 的运行；队列异常只令这一条运行失败。"""
        queue = self._queue_or_raise()
        try:
            job_id = await queue.enqueue_ingestion(ingestion_run_id)
        except IngestionQueueError:
            failed = await self._mark_queue_failed(ingestion_run_id)
            return CollectionBuildRunResponse(
                ingestion_run_id=ingestion_run_id,
                status=IngestionRunStatus(failed.status),
                error_code=failed.error_code,
                error_message=failed.error_message,
            )

        run = await self._record_arq_job_id(ingestion_run_id, job_id)
        return CollectionBuildRunResponse(
            ingestion_run_id=ingestion_run_id,
            status=IngestionRunStatus(run.status),
            arq_job_id=run.arq_job_id,
            error_code=run.error_code,
            error_message=run.error_message,
        )

    async def _assert_submission_quota(
        self,
        *,
        owner_user_id: UUID,
        requested_run_count: int,
    ) -> None:
        """按 UTC 自然日预检本批实际进入构建队列的文献运行额度。"""
        settings = self._settings or get_ingestion_settings()
        now = datetime.now(UTC)
        period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        user_run_count = int(
            await self._session.scalar(
                select(func.count(IngestionRun.id))
                .join(Document, Document.id == IngestionRun.document_id)
                .join(ResearchCollection, ResearchCollection.id == Document.collection_id)
                .where(
                    ResearchCollection.owner_user_id == owner_user_id,
                    IngestionRun.submitted_at >= period_start,
                )
            )
            or 0
        )
        if user_run_count + requested_run_count > settings.rag_user_daily_ingestion_run_limit:
            raise CollectionBuildError(
                CollectionBuildErrorCode.USER_QUOTA_EXCEEDED,
                "今日文献入库额度不足，无法投递本次待构建文献。",
            )
        global_run_count = int(
            await self._session.scalar(
                select(func.count(IngestionRun.id)).where(IngestionRun.submitted_at >= period_start)
            )
            or 0
        )
        if global_run_count + requested_run_count > settings.rag_global_daily_ingestion_run_limit:
            raise CollectionBuildError(
                CollectionBuildErrorCode.GLOBAL_BUDGET_EXHAUSTED,
                "今日全局文献入库预算不足，无法投递本次待构建文献。",
            )

    async def _record_arq_job_id(self, ingestion_run_id: UUID, job_id: str) -> IngestionRun:
        """回写任务标识；Worker 即使很快完成，也保留可审计的投递来源。"""
        run = await self._session.scalar(
            select(IngestionRun).where(IngestionRun.id == ingestion_run_id).with_for_update()
        )
        if run is None:
            raise CollectionBuildError(
                CollectionBuildErrorCode.RUN_NOT_FOUND,
                "入库运行在投递后不存在，无法记录任务状态。",
            )
        run.arq_job_id = job_id
        await self._session.commit()
        return run

    async def _mark_queue_failed(self, ingestion_run_id: UUID) -> IngestionRun:
        """队列不可用时明确标记失败，前端可对单篇文献发起新运行重试。"""
        run = await self._session.scalar(
            select(IngestionRun).where(IngestionRun.id == ingestion_run_id).with_for_update()
        )
        if run is None:
            raise CollectionBuildError(
                CollectionBuildErrorCode.RUN_NOT_FOUND,
                "入库运行不存在，无法记录队列失败。",
            )
        if run.status == IngestionRunStatus.QUEUED.value:
            run.status = IngestionRunStatus.FAILED.value
            run.is_current = False
            run.error_code = "ingestion_queue_unavailable"
            run.error_message = "文献入库任务无法投递，请稍后重试。"
            run.finished_at = datetime.now(UTC)
            run.arq_job_id = None
            await self._session.commit()
        return run

    async def _pending_runs_for_update(self, collection_id: UUID) -> list[IngestionRun]:
        """只锁定活动集合内 pending 运行，归档文献永远不会被重新构建。"""
        rows = await self._session.scalars(
            select(IngestionRun)
            .join(Document, Document.id == IngestionRun.document_id)
            .join(
                CollectionPaper,
                and_(
                    CollectionPaper.collection_id == Document.collection_id,
                    CollectionPaper.paper_id == Document.paper_id,
                ),
            )
            .where(
                Document.collection_id == collection_id,
                CollectionPaper.status == "active",
                IngestionRun.status == IngestionRunStatus.PENDING.value,
            )
            .order_by(IngestionRun.created_at)
            .with_for_update()
        )
        return list(rows)

    async def _owned_failed_run_for_update(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        ingestion_run_id: UUID,
    ) -> tuple[ResearchCollection, IngestionRun]:
        """锁定用户集合中的失败运行，阻止跨工作区重试与同次重复创建。"""
        row = await self._session.execute(
            select(ResearchCollection, IngestionRun)
            .join(Document, Document.collection_id == ResearchCollection.id)
            .join(IngestionRun, IngestionRun.document_id == Document.id)
            .join(
                CollectionPaper,
                and_(
                    CollectionPaper.collection_id == Document.collection_id,
                    CollectionPaper.paper_id == Document.paper_id,
                ),
            )
            .where(
                ResearchCollection.id == collection_id,
                ResearchCollection.owner_user_id == owner_user_id,
                ResearchCollection.status == "active",
                CollectionPaper.status == "active",
                IngestionRun.id == ingestion_run_id,
            )
            .with_for_update(of=(ResearchCollection, IngestionRun))
        )
        result = row.one_or_none()
        if result is None:
            raise CollectionBuildError(
                CollectionBuildErrorCode.RUN_NOT_FOUND,
                "当前工作区中不存在该入库运行。",
            )
        collection, run = result._tuple()
        if run.status != IngestionRunStatus.FAILED.value:
            raise CollectionBuildError(
                CollectionBuildErrorCode.RUN_NOT_RETRYABLE,
                "只有失败的入库运行可以创建新的重试版本。",
            )
        return collection, run

    async def _require_owned_collection(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        lock: bool,
    ) -> ResearchCollection:
        """确认集合属于当前用户且仍活动；所有写操作都同时获取行锁。"""
        statement = select(ResearchCollection).where(
            ResearchCollection.id == collection_id,
            ResearchCollection.owner_user_id == owner_user_id,
            ResearchCollection.status == "active",
        )
        if lock:
            statement = statement.with_for_update()
        collection = await self._session.scalar(statement)
        if collection is None:
            raise CollectionBuildError(
                CollectionBuildErrorCode.COLLECTION_NOT_FOUND,
                "研究集合不存在、已归档或不属于当前用户。",
            )
        return collection

    async def _refresh_collection_stage(self, collection_id: UUID) -> ResearchCollection:
        """重新读取并锁定集合，用最新运行结果决定研究流程展示状态。"""
        collection = await self._session.scalar(
            select(ResearchCollection)
            .where(ResearchCollection.id == collection_id)
            .with_for_update()
        )
        if collection is None:
            raise CollectionBuildError(
                CollectionBuildErrorCode.COLLECTION_NOT_FOUND,
                "研究集合不存在，无法更新构建状态。",
            )
        await self._refresh_collection_stage_locked(collection)
        await self._session.commit()
        return collection

    async def _refresh_collection_stage_locked(self, collection: ResearchCollection) -> None:
        """根据活动文献的运行事实写入集合阶段，不依赖 arq 的临时状态。"""
        rows = await self._session.execute(
            select(IngestionRun.status, IngestionRun.is_current)
            .join(Document, Document.id == IngestionRun.document_id)
            .join(
                CollectionPaper,
                and_(
                    CollectionPaper.collection_id == Document.collection_id,
                    CollectionPaper.paper_id == Document.paper_id,
                ),
            )
            .where(
                Document.collection_id == collection.id,
                CollectionPaper.status == "active",
            )
        )
        runs = list(rows)
        if not runs:
            return

        statuses = {status for status, _is_current in runs}
        if statuses & {
            IngestionRunStatus.PENDING.value,
            IngestionRunStatus.QUEUED.value,
            IngestionRunStatus.RUNNING.value,
        }:
            collection.workflow_stage = WorkspaceWorkflowStage.COLLECTION_BUILDING.value
            return
        if any(
            status == IngestionRunStatus.COMPLETED.value and is_current
            for status, is_current in runs
        ):
            collection.workflow_stage = WorkspaceWorkflowStage.RESEARCHING.value
            return
        collection.workflow_stage = WorkspaceWorkflowStage.FAILED.value

    def _queue_or_raise(self) -> IngestionJobQueue:
        """返回已验证的队列，不能持久化无法被 Worker 领取的 queued 运行。"""
        queue = self._queue
        if queue is None:
            raise RuntimeError("确认构建集合时必须提供入库任务队列")
        return queue
