"""研究计划和多源检索运行的持久化模型。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.workflow.state import ResearchPlanStatus, SearchRunStage, SearchRunStatus

if TYPE_CHECKING:
    from app.db.models.collection import ResearchCollection


class ResearchPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """一个工作区中的可版本化研究计划。"""

    __tablename__ = "research_plans"
    __table_args__ = (
        UniqueConstraint(
            "collection_id",
            "revision",
            name="uq_research_plans_collection_revision",
        ),
        CheckConstraint(
            "status IN ('generating', 'ready', 'confirmed', 'failed', 'superseded')",
            name="status",
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
        Index(
            "ix_research_plans_collection_status_updated_at",
            "collection_id",
            "status",
            "updated_at",
        ),
        {"comment": "用户研究要求解析后的可确认、可版本化研究计划"},
    )

    collection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("research_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属研究工作区标识",
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="同一工作区内递增的研究计划版本号",
    )
    raw_request: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="用户提交的原始自然语言研究要求",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ResearchPlanStatus.GENERATING.value,
        server_default=text("'generating'"),
        index=True,
        comment="计划状态：generating 生成中、ready 待确认、confirmed 已确认、"
        "failed 失败、superseded 已替代",
    )
    direction_options: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment="模型生成的候选研究方向列表，包含标题、说明和子议题",
    )
    selected_direction_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="用户确认的候选方向标识，计划未确认时为空",
    )
    scope: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="用户确认的时间范围、语言和固定准入范围",
    )
    query_plan: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="按文献来源拆分的检索表达式和过滤参数",
    )
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="模型名称、提示词版本和结构化输出配置，不保存密钥",
    )
    arq_job_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        unique=True,
        comment="意图解析 arq 任务标识，计划未投递时为空",
    )
    error_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="计划生成失败的机器可识别错误码"
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="计划生成失败的可展示说明"
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="用户确认计划的时间"
    )

    collection: Mapped[ResearchCollection] = relationship(back_populates="research_plans")
    search_runs: Mapped[list[SearchRun]] = relationship(
        back_populates="research_plan",
        cascade="all, delete-orphan",
    )


class SearchRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """一次多源文献检索的可恢复任务头。"""

    __tablename__ = "search_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'partial_failed', 'failed', "
            "'cancelled', 'expired')",
            name="status",
        ),
        CheckConstraint(
            "stage IN ('dispatch', 'provider_search', 'normalize', 'triage', "
            "'relevance_assessment', 'citation_enrichment', 'completed')",
            name="stage",
        ),
        CheckConstraint("attempt_no > 0", name="attempt_positive"),
        # 同一计划只能同时存在一个排队或运行中的检索，避免重复点击制造并发任务。
        Index(
            "uq_search_runs_active_plan",
            "research_plan_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "ix_search_runs_collection_status_created_at",
            "collection_id",
            "status",
            "created_at",
        ),
        {"comment": "一次多源文献检索的状态、统计和短期 Redis 会话引用"},
    )

    collection_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("research_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属研究工作区标识",
    )
    research_plan_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("research_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="本次检索使用的已确认研究计划标识",
    )
    arq_job_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, comment="检索 arq 任务标识"
    )
    redis_session_key: Mapped[str | None] = mapped_column(
        String(256), nullable=True, unique=True, comment="候选和进度短期存储使用的 Redis 会话键"
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SearchRunStatus.QUEUED.value,
        server_default=text("'queued'"),
        index=True,
        comment="运行状态：queued 排队、running 运行、completed 完成、"
        "partial_failed 部分失败、failed 失败、cancelled 取消、expired 过期",
    )
    stage: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SearchRunStage.DISPATCH.value,
        server_default=text("'dispatch'"),
        index=True,
        comment="处理阶段：dispatch 投递、provider_search 来源检索、normalize 规整、"
        "triage 初筛、relevance_assessment 相关性评估、citation_enrichment 题录补全、"
        "completed 完成",
    )
    attempt_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"), comment="本次运行的重试序号"
    )
    provider_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="各文献来源的状态、耗时、错误和返回数量摘要",
    )
    candidate_counts: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="原始、去重、初筛和最终候选数量统计",
    )
    error_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="检索运行失败的机器可识别错误码"
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="检索运行失败的可展示说明"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Worker 开始执行检索的时间"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="检索运行结束、失败或过期的时间"
    )

    collection: Mapped[ResearchCollection] = relationship(back_populates="search_runs")
    research_plan: Mapped[ResearchPlan] = relationship(back_populates="search_runs")
