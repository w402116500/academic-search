"""研究意图分析的 OpenAI 兼容模型适配器。"""

from langchain_openai import ChatOpenAI

from app.core.workflow_settings import WorkflowSettings
from app.modules.research.intent_analysis import StructuredIntentModel
from app.modules.research.plan_contracts import ResearchPlanDraft


def build_intent_analysis_model(settings: WorkflowSettings) -> StructuredIntentModel:
    """构造 JSON 模式调用器，业务层仍负责 Pydantic 二次校验。"""
    chat_model = ChatOpenAI(
        model=settings.active_chat_model,
        api_key=settings.active_api_key,
        base_url=settings.active_base_url,
        temperature=0,
        timeout=settings.workflow_intent_timeout_seconds,
        max_retries=0,
    )
    return chat_model.with_structured_output(ResearchPlanDraft, method="json_mode")
