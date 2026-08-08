"""研究计划和多源检索运行的持久化模型。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.research.state import ResearchPlanStatus
from app.modules.search.state import SearchRunStage, SearchRunStatus

if TYPE_CHECKING:
    from app.infra.db.models.collection import ResearchCollection


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
        String(256), nullable=True, unique=True, comment="进度事件、锁和可丢缓存使用的 Redis 会话键"
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
    candidates: Mapped[list[SearchRunCandidate]] = relationship(
        back_populates="search_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class SearchRunCandidate(TimestampMixin, Base):
    """一次检索运行下的可恢复候选审核事实。"""

    __tablename__ = "search_run_candidates"
    __table_args__ = (
        PrimaryKeyConstraint(
            "search_run_id",
            "candidate_id",
            name="search_run_candidate_identity",
        ),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint(
            "language IN ('zh', 'en', 'other', 'unknown')",
            name="language",
        ),
        CheckConstraint(
            "relevance_state IN ('pending', 'completed', 'excluded', 'failed', 'skipped')",
            name="relevance_state",
        ),
        CheckConstraint("jsonb_typeof(authors) = 'array'", name="authors"),
        CheckConstraint(
            "jsonb_typeof(citation_counts_by_source) = 'object'", name="citation_counts"
        ),
        CheckConstraint("jsonb_typeof(links) = 'object'", name="links"),
        CheckConstraint("jsonb_typeof(source_refs) = 'array'", name="source_refs"),
        Index(
            "ix_search_run_candidates_run_position",
            "search_run_id",
            "position",
        ),
        Index(
            "ix_search_run_candidates_run_selected_at",
            "search_run_id",
            "selected_at",
        ),
        Index(
            "ix_search_run_candidates_run_relevance_state",
            "search_run_id",
            "relevance_state",
        ),
        {"comment": "Search 边界内可恢复的候选审核事实，不等同于长期论文事实"},
    )

    search_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("search_runs.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属检索运行标识",
    )
    candidate_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
        comment="检索运行内稳定候选标识",
    )
    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="候选在本次检索归并结果中的稳定顺序",
    )
    doi: Mapped[str | None] = mapped_column(
        String(512), nullable=True, index=True, comment="候选 DOI"
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="候选标题")
    title_key: Mapped[str] = mapped_column(Text, nullable=False, comment="候选去重标题键")
    language: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="unknown",
        server_default=text("'unknown'"),
        index=True,
        comment="候选主语言：zh、en、other 或 unknown",
    )
    authors: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment="候选作者展示投影",
    )
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True, comment="候选摘要")
    published_year: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
        index=True,
        comment="候选发表年份",
    )
    published_date: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="候选发表日期结构化投影",
    )
    venue: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="期刊、会议或平台"
    )
    document_type: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="来源文献类型"
    )
    volume: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="卷号")
    issue: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="期号")
    pages: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="页码")
    article_number: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="文章编号"
    )
    publisher: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="出版方")
    citation_counts_by_source: Mapped[dict[str, int]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="各来源可展示引用计数",
    )
    links: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment="候选页面、开放获取和全文链接投影",
    )
    is_open_access: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="来源声明或合并后判断的开放获取状态",
    )
    source_refs: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment="最小来源引用，不保存完整 provider 原始响应",
    )
    triage: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="基础初筛结果",
    )
    relevance_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
        index=True,
        comment="相关性处理状态",
    )
    relevance_assessment: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="已通过证据校验的相关性评估",
    )
    relevance_error: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="相关性终态失败摘要",
    )
    citation: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="候选题录补全投影",
    )
    pdf_availability: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="公开 PDF 可行动状态",
    )
    relevance_retry_attempt_no: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="下一次相关性重试任务应处理该候选的尝试序号",
    )
    selected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="用户加入准备清单的时间；为空表示未选择",
    )

    search_run: Mapped[SearchRun] = relationship(back_populates="candidates")
    fulltext_state: Mapped[SearchCandidateFulltextState | None] = relationship(
        back_populates="candidate_row",
        cascade="all, delete-orphan",
        uselist=False,
        passive_deletes=True,
    )


class SearchCandidateFulltextState(TimestampMixin, Base):
    """检索候选的可恢复全文准备状态。"""

    __tablename__ = "search_candidate_fulltext_states"
    __table_args__ = (
        PrimaryKeyConstraint(
            "search_run_id",
            "candidate_id",
            name="search_candidate_fulltext_state_identity",
        ),
        ForeignKeyConstraint(
            ["search_run_id", "candidate_id"],
            ["search_run_candidates.search_run_id", "search_run_candidates.candidate_id"],
            ondelete="CASCADE",
            name="search_candidate_fulltext_state_candidate",
        ),
        CheckConstraint("attempt_no > 0", name="attempt_positive"),
        CheckConstraint(
            "status IN ('queued', 'downloading', 'validating', 'available', "
            "'requires_upload', 'rejected', 'failed')",
            name="status",
        ),
        CheckConstraint("jsonb_typeof(candidate) = 'object'", name="candidate"),
        Index(
            "ix_search_candidate_fulltext_states_run_status",
            "search_run_id",
            "status",
        ),
        {"comment": "Search 候选进入全文准备阶段后的可恢复状态"},
    )

    search_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
        comment="所属检索运行标识",
    )
    candidate_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
        comment="所属候选标识",
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, comment="全文任务尝试序号")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, comment="全文任务状态"
    )
    candidate: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        comment="全文边界所需的候选投影",
    )
    result_document: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="已校验暂存全文文件投影",
    )
    result_error: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="终态失败的安全错误摘要",
    )
    arq_job_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="候选全文 arq 任务标识",
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="本轮全文准备请求时间",
    )
    state_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="全文状态业务更新时间",
    )

    candidate_row: Mapped[SearchRunCandidate] = relationship(back_populates="fulltext_state")
