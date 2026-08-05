"""研究计划请求、草稿和持久化响应契约。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import TypedDict

from app.modules.research.state import ResearchPlanStatus, WorkspaceWorkflowStage


class ResearchPlanErrorCode(StrEnum):
    """研究计划创建、生成和确认操作的稳定业务错误码。"""

    COLLECTION_NOT_FOUND = "research_plan_collection_not_found"
    COLLECTION_NOT_ACTIVE = "research_plan_collection_not_active"
    PLAN_NOT_FOUND = "research_plan_not_found"
    ANALYSIS_ALREADY_RUNNING = "research_plan_analysis_already_running"
    PLAN_NOT_READY = "research_plan_not_ready"
    PLAN_ALREADY_CONFIRMED = "research_plan_already_confirmed"
    DIRECTION_NOT_FOUND = "research_direction_not_found"
    PLAN_DATA_INVALID = "research_plan_data_invalid"
    QUEUE_UNAVAILABLE = "research_plan_queue_unavailable"


class ResearchPlanError(RuntimeError):
    """研究计划不满足当前操作前置条件时抛出的明确异常。"""

    def __init__(self, code: ResearchPlanErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class StartResearchRequest(BaseModel):
    """首页提交的原始研究要求；工作区名称由服务端从该内容生成。"""

    raw_request: str = Field(min_length=1, max_length=4_000, description="用户的自然语言研究要求")

    @field_validator("raw_request")
    @classmethod
    def normalize_raw_request(cls, value: str) -> str:
        """保留段落语义，拒绝只有空白的输入，避免创建无意义工作区。"""
        paragraphs = [" ".join(paragraph.split()) for paragraph in value.splitlines()]
        normalized = "\n".join(paragraph for paragraph in paragraphs if paragraph).strip()
        if not normalized:
            raise ValueError("研究要求不能为空白")
        return normalized


class RegenerateResearchPlanRequest(StartResearchRequest):
    """用户修改原始研究要求后重新生成一个不可覆盖历史版本的计划。"""


class ResearchLanguage(StrEnum):
    """首版研究计划允许用户主动限定的文献语言。"""

    CHINESE = "zh"  # 中文文献。
    ENGLISH = "en"  # 英文文献。


class ResearchScope(BaseModel):
    """用户确认的检索范围，只包含时间和语言这两项显式选择。"""

    start_year: int | None = Field(default=None, ge=1900, description="自定义时间范围的起始年份")
    end_year: int | None = Field(default=None, ge=1900, description="自定义时间范围的结束年份")
    languages: list[ResearchLanguage] = Field(
        default_factory=lambda: [ResearchLanguage.CHINESE, ResearchLanguage.ENGLISH],
        min_length=1,
        max_length=2,
        description="需要检索的文献主语言",
    )

    @field_validator("languages")
    @classmethod
    def remove_duplicate_languages(cls, value: list[ResearchLanguage]) -> list[ResearchLanguage]:
        """保持用户选择顺序，同时拒绝重复语言造成的歧义。"""
        deduplicated = list(dict.fromkeys(value))
        if len(deduplicated) != len(value):
            raise ValueError("文献语言不能重复选择")
        return deduplicated

    @model_validator(mode="after")
    def validate_year_range(self) -> ResearchScope:
        """自定义年份必须成对填写，且不能指向未来。"""
        if (self.start_year is None) != (self.end_year is None):
            raise ValueError("自定义时间范围必须同时填写起始年份和结束年份")
        if self.start_year is None or self.end_year is None:
            return self

        current_year = datetime.now(UTC).year
        if self.start_year > self.end_year:
            raise ValueError("起始年份不能晚于结束年份")
        if self.end_year > current_year:
            raise ValueError(f"结束年份不能晚于当前年份 {current_year}")
        return self


class ResearchDirection(BaseModel):
    """意图分析产生、供用户选择的一个候选研究方向。"""

    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        description="计划内稳定方向标识，不使用展示标题作为主键",
    )
    title: str = Field(min_length=1, max_length=200, description="用户可读的研究方向名称")
    summary: str = Field(min_length=1, max_length=1_000, description="方向的单句说明")
    subtopics: list[str] = Field(min_length=1, max_length=6, description="用于解释方向的关键子议题")

    @field_validator("title", "summary", mode="before")
    @classmethod
    def trim_required_text(cls, value: object) -> object:
        """防止模型或客户端把仅有空白的文本写入可确认计划。"""
        if isinstance(value, str):
            normalized = " ".join(value.split())
            if not normalized:
                raise ValueError("方向文本不能全为空白")
            return normalized
        return value

    @field_validator("subtopics")
    @classmethod
    def normalize_subtopics(cls, value: list[str]) -> list[str]:
        """去掉空白和重复子议题，使前端可直接作为列表展示。"""
        normalized = [" ".join(topic.split()) for topic in value]
        if not all(normalized):
            raise ValueError("子议题不能为空白")
        if len(set(normalized)) != len(normalized):
            raise ValueError("子议题不能重复")
        return normalized


class ProviderSearchQuery(BaseModel):
    """已确认方向在一个文献来源上的检索表达式与过滤条件。"""

    provider: str = Field(min_length=1, max_length=64, description="Provider Registry 中的来源名称")
    query: str = Field(min_length=1, max_length=2_000, description="传入该来源的检索表达式")
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description="来源特有的年份、语言等过滤参数",
    )

    @field_validator("provider", "query")
    @classmethod
    def trim_query_fields(cls, value: str) -> str:
        """保存前统一去掉首尾空白，防止等价查询产生不必要版本差异。"""
        normalized = value.strip()
        if not normalized:
            raise ValueError("来源名称和检索表达式不能为空白")
        return normalized


class DirectionQueryPlan(BaseModel):
    """一个候选方向对应的来源查询计划，避免选项与检索词错配。"""

    direction_id: str = Field(min_length=1, max_length=64, description="所属候选研究方向标识")
    queries: list[ProviderSearchQuery] = Field(
        min_length=1,
        description="该方向在各文献来源上可执行的检索表达式",
    )


class ResearchPlanDraft(BaseModel):
    """意图分析器必须输出的完整、可供用户确认的计划草稿。"""

    direction_options: list[ResearchDirection] = Field(description="系统提供 2 至 3 个可选研究方向")
    suggested_scope: ResearchScope = Field(description="根据原始问题建议的时间与语言范围")
    direction_query_plans: list[DirectionQueryPlan] = Field(
        description="每个候选方向各自对应的来源检索表达式",
    )

    @field_validator("direction_options")
    @classmethod
    def require_unique_direction_ids(
        cls, value: list[ResearchDirection]
    ) -> list[ResearchDirection]:
        """方向标识在同一计划版本内必须唯一，保证选择动作可审计。"""
        if not 2 <= len(value) <= 3:
            raise ValueError("候选研究方向至少应有 2 个，最多 3 个")
        if len({direction.id for direction in value}) != len(value):
            raise ValueError("候选研究方向标识不能重复")
        return value

    @model_validator(mode="after")
    def require_queries_for_each_direction(self) -> ResearchPlanDraft:
        """每个可选方向必须有且只有一份查询计划，确认后才能确定性执行。"""
        direction_ids = {direction.id for direction in self.direction_options}
        query_plan_ids = {plan.direction_id for plan in self.direction_query_plans}
        if len(query_plan_ids) != len(self.direction_query_plans):
            raise ValueError("同一研究方向不能重复生成查询计划")
        if query_plan_ids != direction_ids:
            raise ValueError("每个候选研究方向都必须有对应的查询计划")
        return self


class ConfirmResearchPlanRequest(BaseModel):
    """用户对计划的唯一确认输入，不能借此篡改系统生成的查询表达式。"""

    selected_direction_id: str = Field(min_length=1, max_length=64)
    scope: ResearchScope


class ResearchPlanScopeSnapshot(TypedDict, total=False):
    """Persisted scope fields without materializing absent optional values."""

    start_year: int | None
    end_year: int | None
    languages: list[ResearchLanguage]


class ResearchPlanScopeEnvelope(TypedDict, total=False):
    """Persisted plan scope wrapper used before and after user confirmation."""

    suggested: ResearchPlanScopeSnapshot
    confirmed: ResearchPlanScopeSnapshot
    admission_rules: dict[str, Any]


class ResearchPlanResponse(BaseModel):
    """研究计划的持久化快照；计划生成中时方向与查询字段允许为空。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    collection_id: UUID
    revision: int
    raw_request: str
    status: ResearchPlanStatus
    direction_options: list[ResearchDirection]
    selected_direction_id: str | None
    scope: ResearchPlanScopeEnvelope
    query_plan: dict[str, Any]
    model_snapshot: dict[str, Any]
    error_code: str | None
    error_message: str | None
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ResearchSubmissionResponse(BaseModel):
    """首页提交成功后的最小恢复信息，前端可据此进入解析中的工作区。"""

    workspace_id: UUID
    workflow_stage: WorkspaceWorkflowStage
    plan: ResearchPlanResponse
