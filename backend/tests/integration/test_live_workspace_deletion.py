"""工作区永久删除在真实 PostgreSQL 约束下的级联验收。"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from app.infra.db.models.collection import CollectionPaper, ResearchCollection
from app.infra.db.models.document import Document, DocumentChunk, IngestionRun
from app.infra.db.models.paper import Paper
from app.infra.db.models.research import Conversation, Message, ResearchEvidence, ResearchRun
from app.infra.db.models.user import User
from app.infra.db.repositories.workspace_deletion import SqlAlchemyWorkspaceDeletionRepository
from app.infra.db.session import async_session_factory
from app.modules.research.state import WorkspaceWorkflowStage
from sqlalchemy import delete, select

_LIVE_TEST_ENVIRONMENT_FLAG = "RUN_LIVE_WORKSPACE_DELETION_TESTS"


def _live_test_is_enabled() -> bool:
    """只有用户显式允许时才向本地 PostgreSQL 写入随机临时记录。"""
    return os.getenv(_LIVE_TEST_ENVIRONMENT_FLAG) == "1"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_delete_root_cascades_evidence_and_preserves_shared_paper() -> None:
    """删除根工作区不能被证据到分块的保护外键阻断，也不能删除共享 Paper。"""
    if not _live_test_is_enabled():
        pytest.skip(f"仅在 {_LIVE_TEST_ENVIRONMENT_FLAG}=1 时运行本地删除集成测试")

    owner_user_id = uuid4()
    collection_id = uuid4()
    paper_id = uuid4()
    document_id = uuid4()
    ingestion_run_id = uuid4()
    chunk_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    research_run_id = uuid4()
    evidence_id = uuid4()

    try:
        async with async_session_factory() as session:
            async with session.begin():
                session.add_all(
                    (
                        User(id=owner_user_id, display_name="Local workspace deletion user"),
                        ResearchCollection(
                            id=collection_id,
                            owner_user_id=owner_user_id,
                            name="Local workspace deletion collection",
                            status="deleting",
                            workflow_stage=WorkspaceWorkflowStage.RESEARCHING.value,
                        ),
                        Paper(
                            id=paper_id,
                            doi=f"10.9999/local-workspace-deletion-{uuid4().hex}",
                            title="Local workspace deletion paper",
                            authors=[{"literal": "Ada Lovelace"}],
                            citation_text="[1] Local workspace deletion paper.",
                            citation_provider="integration-test",
                        ),
                        CollectionPaper(collection_id=collection_id, paper_id=paper_id),
                        Document(
                            id=document_id,
                            collection_id=collection_id,
                            paper_id=paper_id,
                            origin_kind="open_access",
                            original_filename="local-deletion.pdf",
                            media_type="application/pdf",
                            byte_size=1_024,
                            sha256="a" * 64,
                            object_key=f"tests/live-workspace-deletion/{document_id}.pdf",
                            source_url="https://example.test/local-deletion.pdf",
                            access_rights="open_access",
                        ),
                        IngestionRun(
                            id=ingestion_run_id,
                            document_id=document_id,
                            pipeline_version="rag-ingestion-v1",
                            status="completed",
                            stage="index",
                            chunking_config={},
                            embedding_config={},
                            statistics={},
                            attempt_no=1,
                            is_current=True,
                        ),
                        DocumentChunk(
                            id=chunk_id,
                            ingestion_run_id=ingestion_run_id,
                            level=3,
                            ordinal=1,
                            content="Temporary evidence content.",
                            token_count=4,
                            locator={},
                            content_sha256="b" * 64,
                        ),
                        Conversation(
                            id=conversation_id,
                            collection_id=collection_id,
                            owner_user_id=owner_user_id,
                            title="Local workspace deletion conversation",
                        ),
                        Message(
                            id=message_id,
                            conversation_id=conversation_id,
                            role="user",
                            content="Temporary research question.",
                            status="completed",
                            metadata_json={},
                        ),
                        ResearchRun(
                            id=research_run_id,
                            conversation_id=conversation_id,
                            collection_id=collection_id,
                            input_message_id=message_id,
                            mode="single_rag",
                            status="completed",
                            stage="completed",
                            model_config={},
                            retrieval_trace={},
                        ),
                        ResearchEvidence(
                            id=evidence_id,
                            research_run_id=research_run_id,
                            chunk_id=chunk_id,
                            selection_stage="final_citation",
                            is_cited=True,
                        ),
                    )
                )

            deleted = await SqlAlchemyWorkspaceDeletionRepository(session).delete_root(
                owner_user_id=owner_user_id,
                workspace_id=collection_id,
            )

            assert deleted is True
            assert await session.get(ResearchCollection, collection_id) is None
            assert await session.get(Document, document_id) is None
            assert await session.get(IngestionRun, ingestion_run_id) is None
            assert await session.get(DocumentChunk, chunk_id) is None
            assert await session.get(Conversation, conversation_id) is None
            assert await session.get(Message, message_id) is None
            assert await session.get(ResearchRun, research_run_id) is None
            assert await session.get(ResearchEvidence, evidence_id) is None
            assert await session.scalar(select(Paper.id).where(Paper.id == paper_id)) == paper_id
    finally:
        await _clean_up(
            owner_user_id=owner_user_id,
            collection_id=collection_id,
            paper_id=paper_id,
            document_id=document_id,
            ingestion_run_id=ingestion_run_id,
            chunk_id=chunk_id,
            conversation_id=conversation_id,
            message_id=message_id,
            research_run_id=research_run_id,
            evidence_id=evidence_id,
        )


async def _clean_up(
    *,
    owner_user_id: UUID,
    collection_id: UUID,
    paper_id: UUID,
    document_id: UUID,
    ingestion_run_id: UUID,
    chunk_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    research_run_id: UUID,
    evidence_id: UUID,
) -> None:
    """测试失败时按外键方向补偿清理随机临时记录。"""
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(
                delete(ResearchEvidence).where(ResearchEvidence.id == evidence_id)
            )
            await session.execute(delete(ResearchRun).where(ResearchRun.id == research_run_id))
            await session.execute(delete(Message).where(Message.id == message_id))
            await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
            await session.execute(delete(DocumentChunk).where(DocumentChunk.id == chunk_id))
            await session.execute(delete(IngestionRun).where(IngestionRun.id == ingestion_run_id))
            await session.execute(delete(Document).where(Document.id == document_id))
            await session.execute(
                delete(CollectionPaper).where(CollectionPaper.collection_id == collection_id)
            )
            await session.execute(
                delete(ResearchCollection).where(ResearchCollection.id == collection_id)
            )
            await session.execute(delete(User).where(User.id == owner_user_id))
            await session.execute(delete(Paper).where(Paper.id == paper_id))
