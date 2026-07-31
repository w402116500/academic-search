"""研究工作区创建与生命周期管理的输入、输出和错误约定。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.modules.workflow.state import WorkspaceWorkflowStage
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkspaceErrorCode(StrEnum):
    """工作区 API 可安全暴露的业务错误码。"""

    NOT_FOUND = "workspace_not_found"
    NOT_ACTIVE = "workspace_not_active"


class WorkspaceError(RuntimeError):
    """工作区所有权、状态或生命周期不符合操作要求。"""

    def __init__(self, code: WorkspaceErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class CreateWorkspaceRequest(BaseModel):
    """新建研究工作区的最小输入，不允许客户端指定所有者或状态。"""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        """压缩意外换行和重复空格，同时拒绝全空白名称。"""
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("工作区名称不能为空白")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        """空说明按未填写处理，保留正文内有意义的换行。"""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class UpdateWorkspaceRequest(BaseModel):
    """修改活动工作区的显示信息，研究问题将在意图确认功能中单独处理。"""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("工作区名称不能为空白")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def require_at_least_one_change(self) -> UpdateWorkspaceRequest:
        """拒绝空 PATCH，避免客户端把网络重试误报为成功修改。"""
        if not self.model_fields_set:
            raise ValueError("至少需要提供一个可修改字段")
        return self


class WorkspaceResponse(BaseModel):
    """工作区列表和详情共用的可展示字段。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    research_question: str | None
    status: str
    # ``workflow_stage`` 是稳定机器值；中文展示文本由下一字段提供，前端不必自行维护映射。
    workflow_stage: WorkspaceWorkflowStage
    workflow_stage_display: WorkflowStageDisplay
    created_at: datetime
    updated_at: datetime


class WorkflowStageDisplay(BaseModel):
    """工作区研究阶段的中文展示元数据，不作为客户端提交输入。"""

    label: str
    description: str
