"""研究工作流的稳定状态值、中文说明和合法转换规则。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WorkspaceWorkflowStage(StrEnum):
    """工作区在研究主流程中的阶段；英文值会写入数据库和 API。"""

    DRAFT = "draft"  # 草稿：已保存研究要求，但还没有开始解析。
    ANALYZING = "analyzing"  # 意图解析中：正在生成研究对象、关系和候选方向。
    PLAN_REVIEW = "plan_review"  # 计划待确认：等待用户确认方向、时间范围和语言。
    RETRIEVING = "retrieving"  # 文献检索中：正在调用多源 Provider 获取候选。
    SCREENING = "screening"  # 文献筛选中：正在规整候选，或等待用户审核结果。
    COLLECTION_BUILDING = "collection_building"  # 集合构建中：正在获取全文并建立 RAG 索引。
    RESEARCHING = "researching"  # 研究中：当前工作区已有可问答的索引文献。
    FAILED = "failed"  # 失败：当前阶段无法继续，等待用户重试或修改条件。


class ResearchPlanStatus(StrEnum):
    """研究计划的生成和确认状态。"""

    GENERATING = "generating"  # 生成中：模型或规则正在整理候选研究方向。
    READY = "ready"  # 待确认：已生成方向，等待用户选择并确认范围。
    CONFIRMED = "confirmed"  # 已确认：用户已确认方向和检索范围。
    FAILED = "failed"  # 生成失败：计划不完整或模型调用失败，可重新生成。
    SUPERSEDED = "superseded"  # 已替代：该版本被用户后续生成的新计划取代。


class SearchRunStatus(StrEnum):
    """一次多源文献检索运行的整体状态。"""

    QUEUED = "queued"  # 排队中：已创建运行，等待 Worker 获取任务。
    RUNNING = "running"  # 运行中：至少一个检索或处理阶段正在执行。
    COMPLETED = "completed"  # 已完成：候选处理链正常结束。
    PARTIAL_FAILED = "partial_failed"  # 部分失败：部分来源失败，但仍有结果可展示。
    FAILED = "failed"  # 失败：没有可恢复的结果或所有来源均失败。
    CANCELLED = "cancelled"  # 已取消：用户或系统主动停止运行。
    EXPIRED = "expired"  # 已过期：Redis 中的短期候选会话已过期。


class SearchRunStage(StrEnum):
    """检索运行中可对用户展示的确定性阶段。"""

    DISPATCH = "dispatch"  # 任务投递：准备并发调用已启用的 Provider。
    PROVIDER_SEARCH = "provider_search"  # 来源检索：各 Provider 独立请求和报告状态。
    NORMALIZE = "normalize"  # 结果规整：统一标题、作者、年份、DOI 和链接字段。
    TRIAGE = "triage"  # 元数据初筛：去重、基础准入和候选质量检查。
    RELEVANCE_ASSESSMENT = "relevance_assessment"  # 语义评估：依据标题和摘要解释候选相关性。
    CITATION_ENRICHMENT = "citation_enrichment"  # 题录补全：按需获取格式中立的正式题录。
    COMPLETED = "completed"  # 处理完成：结果可供前端读取和用户审核。


@dataclass(frozen=True, slots=True)
class WorkflowStagePresentation:
    """给前端和日志使用的中文阶段说明，不参与状态判断。"""

    label: str
    description: str


WORKFLOW_STAGE_PRESENTATIONS: dict[WorkspaceWorkflowStage, WorkflowStagePresentation] = {
    WorkspaceWorkflowStage.DRAFT: WorkflowStagePresentation(
        label="研究草稿",
        description="已保存研究要求，尚未开始解析。",
    ),
    WorkspaceWorkflowStage.ANALYZING: WorkflowStagePresentation(
        label="正在解析研究要求",
        description="系统正在识别研究对象、关系和可检索概念。",
    ),
    WorkspaceWorkflowStage.PLAN_REVIEW: WorkflowStagePresentation(
        label="等待确认研究计划",
        description="请确认研究方向、时间范围和语言范围。",
    ),
    WorkspaceWorkflowStage.RETRIEVING: WorkflowStagePresentation(
        label="正在检索文献",
        description="系统正在向已启用的文献来源发送检索请求。",
    ),
    WorkspaceWorkflowStage.SCREENING: WorkflowStagePresentation(
        label="正在筛选文献",
        description="系统正在规整候选，随后可由用户审核和选择。",
    ),
    WorkspaceWorkflowStage.COLLECTION_BUILDING: WorkflowStagePresentation(
        label="正在构建研究集合",
        description="系统正在获取全文、解析、切块、嵌入并建立索引。",
    ),
    WorkspaceWorkflowStage.RESEARCHING: WorkflowStagePresentation(
        label="可以开始研究",
        description="当前工作区已有完成索引的全文，可进入证据研究。",
    ),
    WorkspaceWorkflowStage.FAILED: WorkflowStagePresentation(
        label="任务需要处理",
        description="当前阶段执行失败，可查看原因并重试或修改条件。",
    ),
}


# 每个转换都由服务端校验；前端不能通过提交任意阶段值跳过确认步骤。
WORKFLOW_STAGE_TRANSITIONS: dict[WorkspaceWorkflowStage, frozenset[WorkspaceWorkflowStage]] = {
    WorkspaceWorkflowStage.DRAFT: frozenset({WorkspaceWorkflowStage.ANALYZING}),
    WorkspaceWorkflowStage.ANALYZING: frozenset(
        {WorkspaceWorkflowStage.PLAN_REVIEW, WorkspaceWorkflowStage.FAILED}
    ),
    WorkspaceWorkflowStage.PLAN_REVIEW: frozenset(
        {WorkspaceWorkflowStage.ANALYZING, WorkspaceWorkflowStage.RETRIEVING}
    ),
    WorkspaceWorkflowStage.RETRIEVING: frozenset(
        {WorkspaceWorkflowStage.SCREENING, WorkspaceWorkflowStage.FAILED}
    ),
    WorkspaceWorkflowStage.SCREENING: frozenset(
        {
            WorkspaceWorkflowStage.RETRIEVING,
            WorkspaceWorkflowStage.COLLECTION_BUILDING,
            WorkspaceWorkflowStage.FAILED,
        }
    ),
    WorkspaceWorkflowStage.COLLECTION_BUILDING: frozenset(
        {WorkspaceWorkflowStage.RESEARCHING, WorkspaceWorkflowStage.FAILED}
    ),
    WorkspaceWorkflowStage.RESEARCHING: frozenset({WorkspaceWorkflowStage.COLLECTION_BUILDING}),
    WorkspaceWorkflowStage.FAILED: frozenset(
        {
            WorkspaceWorkflowStage.ANALYZING,
            WorkspaceWorkflowStage.RETRIEVING,
            WorkspaceWorkflowStage.COLLECTION_BUILDING,
        }
    ),
}


class InvalidWorkflowTransition(ValueError):
    """工作区试图跳过确认或逆向进入不允许阶段时抛出。"""


def _coerce_stage(stage: WorkspaceWorkflowStage | str) -> WorkspaceWorkflowStage:
    """把数据库字符串转换为受约束的内部状态枚举。"""
    try:
        return WorkspaceWorkflowStage(stage)
    except ValueError as exc:
        raise InvalidWorkflowTransition(f"未知的工作流阶段：{stage}") from exc


def assert_workflow_transition(
    current: WorkspaceWorkflowStage | str,
    target: WorkspaceWorkflowStage | str,
) -> None:
    """验证一次工作区阶段转换，错误消息同时包含中文阶段含义。"""
    current_stage = _coerce_stage(current)
    target_stage = _coerce_stage(target)
    if target_stage not in WORKFLOW_STAGE_TRANSITIONS[current_stage]:
        current_label = WORKFLOW_STAGE_PRESENTATIONS[current_stage].label
        target_label = WORKFLOW_STAGE_PRESENTATIONS[target_stage].label
        raise InvalidWorkflowTransition(
            f"不能从“{current_label}”转换到“{target_label}”（{current_stage} -> {target_stage}）。"
        )


def get_workflow_stage_presentation(
    stage: WorkspaceWorkflowStage | str,
) -> WorkflowStagePresentation:
    """返回阶段的中文标签和说明，供 API 序列化和日志使用。"""
    return WORKFLOW_STAGE_PRESENTATIONS[_coerce_stage(stage)]
