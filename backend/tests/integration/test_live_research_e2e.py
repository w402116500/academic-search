"""真实 RAG 研究 Worker 验收：检索、回答、引用、事件与清理。"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.core.ingestion_settings import get_ingestion_settings
from app.core.workflow_settings import get_workflow_settings
from app.infra.db.models.collection import CollectionPaper, ResearchCollection
from app.infra.db.models.document import Document, DocumentChunk, IngestionRun
from app.infra.db.models.paper import Paper
from app.infra.db.models.research import ResearchRun
from app.infra.db.models.user import User
from app.infra.db.repositories.research_conversations import (
    SqlAlchemyResearchConversationAdapter,
)
from app.infra.db.session import async_session_factory
from app.infra.llm.embeddings import OpenAICompatibleTextEmbedder
from app.infra.milvus.document_chunks import MilvusDocumentChunkIndex
from app.infra.redis.connection import redis_client_from_environment
from app.infra.redis.research_events import RedisResearchEventStore
from app.modules.rag.ingestion.contracts import EmbeddedVectorChunk, VectorChunk
from app.modules.research.contracts import CreateConversationRequest, ResearchRunStatus
from app.modules.research.events import build_research_event_stream_key
from app.modules.research.settings import get_research_settings
from app.modules.research.state import WorkspaceWorkflowStage
from app.workers.research import run_research, startup
from sqlalchemy import delete, text

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_RESEARCH_E2E_TESTS"
_EVIDENCE_TEXTS = (
    """
    Self-attention, sometimes called intra-attention, relates different positions of a single
    sequence in order to compute a representation of the sequence. The Transformer relies
    entirely on self-attention to compute representations of its input and output.
    """.strip(),
    """
    Multi-head attention allows the model to jointly attend to information from different
    representation subspaces at different positions, so several attention operations can be
    performed in parallel. In addition to attention sub-layers, each encoder and decoder layer
    contains a fully connected feed-forward network.
    """.strip(),
)


class CapturingResearchQueue:
    """保留服务层投递结果，随后由测试主动调用同一 Worker 函数。"""

    def __init__(self) -> None:
        self.enqueued_run_ids: list[UUID] = []

    async def enqueue_research(self, research_run_id: UUID, *, retry: bool = False) -> str:
        self.enqueued_run_ids.append(research_run_id)
        suffix = "-retry" if retry else ""
        return f"live-research-{research_run_id}{suffix}"


def _live_test_is_enabled() -> bool:
    """外部模型调用和本地基础设施写入必须由显式环境变量开启。"""
    return os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) == "1"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_research_worker_returns_citable_answer() -> None:
    """真实模型回答只能引用当前集合的原文，并留下可重放的公开阶段事件。"""
    if not _live_test_is_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行真实 RAG 验收")

    ingestion_settings = get_ingestion_settings()
    research_settings = get_research_settings()
    assert research_settings.reranker_enabled
    embedder = OpenAICompatibleTextEmbedder(ingestion_settings)
    vector_index = MilvusDocumentChunkIndex(ingestion_settings)
    owner_user_id, collection_id, paper_id, document_id, ingestion_run_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    chunk_ids = (uuid4(), uuid4())
    research_run_id: UUID | None = None
    langgraph_thread_id: str | None = None

    try:
        # 先建成 completed/current 版本，再写 Milvus，符合线上检索的准入顺序。
        async with async_session_factory() as session:
            async with session.begin():
                session.add_all(
                    (
                        User(id=owner_user_id, display_name="Live research test user"),
                        ResearchCollection(
                            id=collection_id,
                            owner_user_id=owner_user_id,
                            name="Live RAG evidence test collection",
                            workflow_stage=WorkspaceWorkflowStage.RESEARCHING.value,
                        ),
                        Paper(
                            id=paper_id,
                            doi=f"10.48550/arXiv.1706.03762-live-{uuid4().hex}",
                            title="Attention Is All You Need",
                            authors=[{"given": "Ashish", "family": "Vaswani"}],
                            abstract="A Transformer architecture based on attention mechanisms.",
                            publication_year=2017,
                            venue="Advances in Neural Information Processing Systems",
                            paper_type="conference_paper",
                            official_url="https://arxiv.org/abs/1706.03762",
                            language="en",
                            citation_text="Vaswani A, et al. Attention Is All You Need[C]. 2017.",
                            citation_provider="arxiv",
                            citation_source_url="https://arxiv.org/abs/1706.03762",
                        ),
                    )
                )
                session.add(
                    CollectionPaper(
                        collection_id=collection_id,
                        paper_id=paper_id,
                        status="active",
                        tags=[],
                    )
                )
                session.add(
                    Document(
                        id=document_id,
                        collection_id=collection_id,
                        paper_id=paper_id,
                        origin_kind="open_access",
                        original_filename="attention-is-all-you-need.pdf",
                        media_type="application/pdf",
                        byte_size=2_215_244,
                        sha256=hashlib.sha256("\n".join(_EVIDENCE_TEXTS).encode()).hexdigest(),
                        object_key=f"live-research/{document_id}.pdf",
                        source_url="https://arxiv.org/pdf/1706.03762",
                        access_rights="open_access",
                    )
                )
                session.add(
                    IngestionRun(
                        id=ingestion_run_id,
                        document_id=document_id,
                        pipeline_version="live-research-e2e-v1",
                        status="completed",
                        stage="index",
                        chunking_config={"source": "live research fixture"},
                        embedding_config=ingestion_settings.embedding_snapshot,
                        statistics={"vector_dimension": 1024},
                        attempt_no=1,
                        is_current=True,
                        started_at=datetime.now(UTC),
                        finished_at=datetime.now(UTC),
                    )
                )
                session.add_all(
                    DocumentChunk(
                        id=chunk_id,
                        ingestion_run_id=ingestion_run_id,
                        parent_chunk_id=None,
                        root_chunk_id=None,
                        level=3,
                        ordinal=index,
                        content=content,
                        token_count=len(content.split()),
                        page_start=1,
                        page_end=1,
                        section_path=["Transformer architecture"],
                        locator={"paragraph": index},
                        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                    )
                    for index, (chunk_id, content) in enumerate(
                        zip(chunk_ids, _EVIDENCE_TEXTS, strict=True), start=1
                    )
                )

        embeddings = await embedder.embed_documents(_EVIDENCE_TEXTS)
        assert len(embeddings) == len(_EVIDENCE_TEXTS)
        assert all(len(vector) == 1_024 for vector in embeddings)
        await vector_index.upsert(
            tuple(
                EmbeddedVectorChunk(
                    chunk=VectorChunk(
                        chunk_id=chunk_id,
                        owner_user_id=owner_user_id,
                        collection_id=collection_id,
                        document_id=document_id,
                        ingestion_run_id=ingestion_run_id,
                        level=3,
                        content=content,
                    ),
                    embedding=embedding,
                )
                for chunk_id, content, embedding in zip(
                    chunk_ids, _EVIDENCE_TEXTS, embeddings, strict=True
                )
            )
        )

        queue = CapturingResearchQueue()
        async with async_session_factory() as session:
            service = SqlAlchemyResearchConversationAdapter(session, queue)
            conversation = await service.create_conversation(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                request=CreateConversationRequest(title="Transformer 原文研究"),
            )
            asked = await service.ask_question(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                conversation_id=conversation.id,
                content=(
                    "根据文中原文，self-attention 与 multi-head attention "
                    "在 Transformer 中分别有什么作用？"
                ),
                model_config=get_workflow_settings().model_snapshot,
            )
            research_run_id = asked.research_run.id
            assert queue.enqueued_run_ids == [research_run_id]

        # 直接调用真实 Worker 函数可稳定覆盖 Worker 的领取、checkpoint、模型和事件逻辑。
        worker_context: dict[str, object] = {}
        await startup(worker_context)
        worker_result = await run_research(worker_context, str(research_run_id))
        assert worker_result["status"] == ResearchRunStatus.COMPLETED.value

        async with async_session_factory() as session:
            run = await SqlAlchemyResearchConversationAdapter(session).get_run(
                owner_user_id=owner_user_id,
                collection_id=collection_id,
                conversation_id=conversation.id,
                research_run_id=research_run_id,
            )
            assert run.status is ResearchRunStatus.COMPLETED
            assert run.output_message_id is not None
            assert run.evidences
            assert {evidence.chunk_id for evidence in run.evidences}.issubset(set(chunk_ids))
            assert all(evidence.citation_excerpt for evidence in run.evidences)
            assert all(evidence.locator_snapshot for evidence in run.evidences)
            claim_verification = run.retrieval_trace["answer_claim_verification"]
            assert claim_verification["status"] == "supported"
            assert claim_verification["claim_count"] > 0
            assert claim_verification["unsupported_claim_count"] == 0
            reranker_trace = run.retrieval_trace["reranker"]
            assert isinstance(reranker_trace, dict)
            assert reranker_trace["enabled"] is True
            assert reranker_trace["status"] == "completed"
            assert reranker_trace["adapter"] == "http_reranker"
            candidate_count = reranker_trace["candidate_count"]
            returned_count = reranker_trace["returned_count"]
            assert isinstance(candidate_count, int)
            assert isinstance(returned_count, int)
            assert candidate_count >= returned_count >= 1
            langgraph_thread_id = f"research-{research_run_id}"

        redis = redis_client_from_environment()
        try:
            events = await RedisResearchEventStore(
                redis,
                ttl_seconds=get_research_settings().rag_event_ttl_seconds,
            ).read_events(research_run_id, last_event_id="0-0", block_milliseconds=1)
        finally:
            await redis.aclose()
        assert any(event[1].get("status") == ResearchRunStatus.COMPLETED.value for event in events)

        print(
            json.dumps(
                {
                    "research_run_id": str(research_run_id),
                    "status": worker_result["status"],
                    "cited_evidence_count": len(run.evidences),
                    "answer_preview": run.retrieval_trace,
                    "event_count": len(events),
                    "cleanup": "pending",
                },
                ensure_ascii=False,
            )
        )
    finally:
        # 清理只按本次随机运行、工作区与 LangGraph thread 进行，绝不影响已有研究数据。
        await vector_index.delete_ingestion_run(ingestion_run_id)
        if research_run_id is not None:
            redis = redis_client_from_environment()
            try:
                await redis.delete(build_research_event_stream_key(research_run_id))
            finally:
                await redis.aclose()
        async with async_session_factory() as session:
            async with session.begin():
                if langgraph_thread_id is not None:
                    for table_name in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                        await session.execute(
                            text(f"DELETE FROM {table_name} WHERE thread_id = :thread_id"),
                            {"thread_id": langgraph_thread_id},
                        )
                # 使用数据库级联而不是 ORM 实体删除，覆盖真实运行已经写入
                # conversation、messages、research_runs 与 research_evidences 的场景。
                if research_run_id is not None:
                    await session.execute(
                        delete(ResearchRun).where(ResearchRun.id == research_run_id)
                    )
                await session.execute(
                    delete(ResearchCollection).where(ResearchCollection.id == collection_id)
                )
                await session.execute(delete(Paper).where(Paper.id == paper_id))
                await session.execute(delete(User).where(User.id == owner_user_id))

        print(
            json.dumps(
                {
                    "research_run_id": str(research_run_id) if research_run_id else None,
                    "ingestion_run_id": str(ingestion_run_id),
                    "cleanup": "deleted",
                },
                ensure_ascii=False,
            )
        )
