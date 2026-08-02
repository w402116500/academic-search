"""研究会话、运行状态和可引用证据的 API 契约。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConversationStatus(StrEnum):
    """研究会话的稳定生命周期状态。"""

    ACTIVE = "active"  # 当前可继续提问的会话。
    ARCHIVED = "archived"  # 用户暂时收起的历史会话。
    DELETED = "deleted"  # 用户软删除的会话，不再出现在普通列表中。


class ResearchRunMode(StrEnum):
    """研究运行使用的受控图模式。"""

    SINGLE_RAG = "single_rag"  # 单问题证据问答。
    MULTI_AGENT = "multi_agent"  # 多子问题的跨论文分析。
    RESEARCH_NOTE = "research_note"  # 结构化研究笔记。


class ResearchRunStatus(StrEnum):
    """研究运行写入 PostgreSQL 的稳定状态值。"""

    QUEUED = "queued"  # 已持久化，等待研究 Worker。
    RUNNING = "running"  # Worker 正在调用受限图。
    AWAITING_CLARIFICATION = "awaiting_clarification"  # 当前集合证据不足，需要用户补充。
    COMPLETED = "completed"  # 已生成可追溯回答。
    FAILED = "failed"  # 基础设施或模型失败，可重试。
    CANCELLED = "cancelled"  # 在 Worker 领取前被用户取消。


class ResearchRunStage(StrEnum):
    """可公开展示的运行阶段，不包含模型内部思维链。"""

    DISPATCH = "dispatch"  # 已接收问题，等待任务投递。
    PREPARING = "preparing"  # 正在校验集合、会话和当前文档版本。
    HYBRID_RETRIEVAL = "hybrid_retrieval"  # 正在执行向量与关键词召回。
    PARENT_MERGING = "parent_merging"  # 正在合并同一论文的父块上下文。
    RERANKING = "reranking"  # 正在融合并筛选候选证据。
    EVIDENCE_VERIFYING = "evidence_verifying"  # 正在检查证据是否支持结论。
    ANSWERING = "answering"  # 正在仅基于入选证据组织回答。
    COMPLETED = "completed"  # 已完成并可查看引用。
    AWAITING_CLARIFICATION = "awaiting_clarification"  # 需要补充研究对象或范围。
    FAILED = "failed"  # 执行失败。
    CANCELLED = "cancelled"  # 已取消。


class ResearchRunStageDisplay(BaseModel):
    """前端显示的中文阶段说明，英文值仍是机器判断依据。"""

    label: str
    description: str


RESEARCH_RUN_STAGE_DISPLAYS: dict[ResearchRunStage, ResearchRunStageDisplay] = {
    ResearchRunStage.DISPATCH: ResearchRunStageDisplay(
        label="等待研究任务", description="问题已保存，正在投递研究任务。"
    ),
    ResearchRunStage.PREPARING: ResearchRunStageDisplay(
        label="准备证据范围", description="正在确认当前集合和可检索文献版本。"
    ),
    ResearchRunStage.HYBRID_RETRIEVAL: ResearchRunStageDisplay(
        label="检索原文证据", description="正在从当前集合召回相关原文片段。"
    ),
    ResearchRunStage.PARENT_MERGING: ResearchRunStageDisplay(
        label="补全上下文", description="正在合并同一论文中的相邻父块上下文。"
    ),
    ResearchRunStage.RERANKING: ResearchRunStageDisplay(
        label="筛选证据", description="正在融合召回结果并保留相关证据。"
    ),
    ResearchRunStage.EVIDENCE_VERIFYING: ResearchRunStageDisplay(
        label="核验证据", description="正在检查原文是否足以支持准备输出的结论。"
    ),
    ResearchRunStage.ANSWERING: ResearchRunStageDisplay(
        label="整理回答", description="正在仅依据已核验证据生成回答。"
    ),
    ResearchRunStage.COMPLETED: ResearchRunStageDisplay(
        label="回答已完成", description="回答中的引用可回到当前集合的原文位置。"
    ),
    ResearchRunStage.AWAITING_CLARIFICATION: ResearchRunStageDisplay(
        label="需要补充问题", description="当前集合没有足够证据支持直接回答。"
    ),
    ResearchRunStage.FAILED: ResearchRunStageDisplay(
        label="研究任务失败", description="执行失败，可查看原因后重试。"
    ),
    ResearchRunStage.CANCELLED: ResearchRunStageDisplay(
        label="研究任务已取消", description="任务在执行前被取消，不会生成回答。"
    ),
}


class ResearchErrorCode(StrEnum):
    """研究会话服务可安全返回给 API 的失败类别。"""

    COLLECTION_NOT_FOUND = "research_collection_not_found"
    CONVERSATION_NOT_FOUND = "research_conversation_not_found"
    CONVERSATION_UNAVAILABLE = "research_conversation_unavailable"
    NO_RESEARCHABLE_DOCUMENTS = "research_no_researchable_documents"
    RUN_NOT_FOUND = "research_run_not_found"
    RUN_NOT_RETRYABLE = "research_run_not_retryable"
    RUN_NOT_CANCELLABLE = "research_run_not_cancellable"
    QUEUE_UNAVAILABLE = "research_queue_unavailable"


class ResearchError(RuntimeError):
    """会话归属、运行状态或任务队列不满足前置条件时抛出。"""

    def __init__(self, code: ResearchErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class CreateConversationRequest(BaseModel):
    """在一个可研究集合内创建会话的输入。"""

    title: str | None = Field(default=None, max_length=300, description="用户可选的会话标题")

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        """空标题由服务端在收到首个问题后生成，不写入空白字符串。"""
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


class AskResearchQuestionRequest(BaseModel):
    """用户提交给当前研究会话的问题。"""

    content: str = Field(min_length=1, max_length=8_000, description="需要从当前集合核验的问题")

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        """保留分段语义，拒绝只含空白的研究问题。"""
        paragraphs = [" ".join(paragraph.split()) for paragraph in value.splitlines()]
        normalized = "\n".join(paragraph for paragraph in paragraphs if paragraph).strip()
        if not normalized:
            raise ValueError("研究问题不能为空白")
        return normalized


class ConversationResponse(BaseModel):
    """会话列表和详情共用的持久化摘要。"""

    id: UUID
    collection_id: UUID
    title: str | None
    status: ConversationStatus
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ResearchMessageResponse(BaseModel):
    """消息正文和当前展示状态；证据通过运行响应单独返回。"""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    conversation_id: UUID
    role: str
    content: str
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    created_at: datetime
    research_run_id: UUID | None = None


class ResearchEvidenceResponse(BaseModel):
    """回答引用的可追溯原文证据。"""

    id: UUID
    chunk_id: UUID
    selection_stage: str
    rank: int | None
    vector_score: float | None
    rrf_score: float | None
    rerank_score: float | None
    is_cited: bool
    citation_excerpt: str | None
    locator_snapshot: dict[str, Any] | None
    paper_id: UUID
    title: str
    authors: list[dict[str, Any]]
    publication_year: int | None
    source_url: str | None


class ResearchRunResponse(BaseModel):
    """可恢复研究运行及其用户可见阶段。"""

    id: UUID
    conversation_id: UUID
    collection_id: UUID
    input_message_id: UUID
    output_message_id: UUID | None
    arq_job_id: str | None
    mode: ResearchRunMode
    status: ResearchRunStatus
    stage: ResearchRunStage
    stage_display: ResearchRunStageDisplay
    model_snapshot: dict[str, Any]
    retrieval_trace: dict[str, Any]
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    evidences: list[ResearchEvidenceResponse] = Field(default_factory=list)


class ConversationDetailResponse(BaseModel):
    """单个会话的消息历史及每条问题对应的运行。"""

    conversation: ConversationResponse
    messages: list[ResearchMessageResponse]
    runs: list[ResearchRunResponse]


class AskResearchQuestionResponse(BaseModel):
    """问题已持久化并排队后的恢复标识。"""

    user_message: ResearchMessageResponse
    research_run: ResearchRunResponse


class ResearchProgressEvent(BaseModel):
    """Redis Stream 和 SSE 使用的公开研究进度事件。"""

    run_id: UUID
    status: ResearchRunStatus
    stage: ResearchRunStage
    message: str | None = None
    evidence_count: int = 0
