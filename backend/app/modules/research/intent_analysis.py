"""受 Pydantic 契约约束的 OpenAI 兼容研究意图分析适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from app.core.workflow_settings import WorkflowSettings
from app.modules.research.plan_contracts import ResearchPlanDraft


class IntentAnalysisErrorCode(StrEnum):
    """意图分析边界可识别、可展示的失败类别。"""

    MODEL_REQUEST_FAILED = "intent_model_request_failed"
    OUTPUT_INVALID = "intent_model_output_invalid"


class IntentAnalysisError(RuntimeError):
    """模型调用失败或返回不符合研究计划契约时抛出的业务异常。"""

    def __init__(self, code: IntentAnalysisErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class StructuredIntentModel(Protocol):
    """意图分析器依赖的最小 LangChain 异步调用接口，便于离线替换。"""

    async def ainvoke(self, input: list[SystemMessage | HumanMessage]) -> object:
        """根据系统和用户消息返回 JSON 对象或 Pydantic 计划草稿。"""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class IntentAnalysisResult:
    """分析器返回的业务草稿和系统生成的模型审计快照。"""

    draft: ResearchPlanDraft
    model_snapshot: dict[str, str | float]


_SYSTEM_PROMPT = (
    "你是学术文献研究的意图分析器。请把用户的原始研究要求整理为一个可确认的研究计划草稿。\n\n"
    "约束：\n"
    "1. 只输出由请求的 JSON schema 描述的数据，不输出解释、Markdown 或推理过程。\n"
    "2. 必须生成 2 到 3 个真正不同的研究方向。每个方向有简洁中文标题、中文单句说明，"
    "以及 1 到 6 个中文关键子议题。\n"
    "3. 每个方向必须提供独立的 direction_query_plans。每份计划至少有一个来源查询；"
    "查询可使用英语或中英混合的学术检索词，不能使用完整自然语言问题替代查询。\n"
    "4. suggested_scope 只能建议 start_year、end_year 和 languages。没有可靠的年份限制时"
    "可不填写年份；结束年份不得晚于当前年份。\n"
    "5. 不要承诺文献质量、全文可用性或研究结论。正式准入仍由后续 DOI、题录和全文核验处理。\n"
    "6. 顶层必须且只能使用 direction_options、suggested_scope、direction_query_plans 三个字段，"
    "不能再包装 research_plan_draft、data 或 result 字段。\n"
    "7. direction_options 的每一项只能使用 id、title、summary、subtopics；id 使用小写英文、数字、"
    "连字符或下划线。direction_query_plans 的每一项只能使用 direction_id、queries；"
    "queries 的每一项只能使用 provider、query，其中 provider 使用 openalex、crossref、arxiv 或"
    "semantic_scholar，query 是单条字符串而非字符串数组。\n"
    "正确形状示例："
    '{"direction_options":[{"id":"green-access","title":"...","summary":"...",'
    '"subtopics":["..."]}],"suggested_scope":{"start_year":2018,"end_year":2025,'
    '"languages":["zh","en"]},"direction_query_plans":[{"direction_id":"green-access",'
    '"queries":[{"provider":"openalex","query":"green space accessibility mental health"}]}]}'
)


class ResearchIntentAnalyzer:
    """调用已装配的结构化模型，并二次校验研究计划输出。"""

    def __init__(
        self,
        settings: WorkflowSettings,
        *,
        model: StructuredIntentModel,
    ) -> None:
        self._settings = settings
        self._model = model

    async def analyze(self, raw_request: str) -> IntentAnalysisResult:
        """生成可确认的计划草稿；外部模型错误不会被伪装成空结果。"""
        messages: list[SystemMessage | HumanMessage] = [
            SystemMessage(_SYSTEM_PROMPT),
            HumanMessage(raw_request),
        ]
        try:
            raw_result = await self._model.ainvoke(messages)
        except OutputParserException as exc:
            # JSON mode 已返回内容但未遵守结构时，用户应看到可重新生成的计划错误而非网络故障。
            raise IntentAnalysisError(
                IntentAnalysisErrorCode.OUTPUT_INVALID,
                "研究意图分析结果不符合计划结构，未生成可确认的检索计划。",
            ) from exc
        except Exception as exc:
            # 第三方 OpenAI 兼容网关的异常类型并不稳定，在适配器边界统一为明确失败。
            raise IntentAnalysisError(
                IntentAnalysisErrorCode.MODEL_REQUEST_FAILED,
                "研究意图分析模型暂时不可用，未生成检索计划。",
            ) from exc

        try:
            draft = ResearchPlanDraft.model_validate(raw_result)
        except ValidationError as exc:
            raise IntentAnalysisError(
                IntentAnalysisErrorCode.OUTPUT_INVALID,
                "研究意图分析结果不完整，未生成可确认的检索计划。",
            ) from exc

        return IntentAnalysisResult(draft=draft, model_snapshot=self._settings.model_snapshot)
