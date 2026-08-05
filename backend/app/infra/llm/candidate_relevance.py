"""候选相关性评估与独立核验的 OpenAI 兼容模型适配器。"""

from functools import partial

from langchain_openai import ChatOpenAI

from app.core.workflow_settings import WorkflowSettings
from app.modules.search.relevance import (
    CandidateRelevanceEvaluator,
    StructuredCandidateRelevanceClaimVerifier,
    StructuredRelevanceModel,
)


def build_candidate_relevance_model(
    settings: WorkflowSettings,
    candidate_count: int,
) -> StructuredRelevanceModel:
    """按完整候选集合规模配置评估输出预算，不拆分集合。"""
    chat_model = ChatOpenAI(
        model=settings.active_chat_model,
        api_key=settings.active_api_key,
        base_url=settings.active_base_url,
        temperature=0,
        max_retries=0,
        max_tokens=(settings.workflow_relevance_output_tokens_per_candidate * candidate_count),  # pyright: ignore[reportCallIssue]
    )
    return chat_model.bind(response_format={"type": "json_object"})  # type: ignore[return-value]


def build_candidate_relevance_verification_model(
    settings: WorkflowSettings,
    candidate_count: int,
) -> StructuredRelevanceModel:
    """为独立主张核验配置与评估调用分离的输出预算。"""
    chat_model = ChatOpenAI(
        model=settings.active_chat_model,
        api_key=settings.active_api_key,
        base_url=settings.active_base_url,
        temperature=0,
        max_retries=0,
        max_tokens=(  # pyright: ignore[reportCallIssue]
            settings.workflow_relevance_verification_output_tokens_per_candidate * candidate_count
        ),
    )
    return chat_model.bind(response_format={"type": "json_object"})  # type: ignore[return-value]


def build_candidate_relevance_evaluator(
    settings: WorkflowSettings,
) -> CandidateRelevanceEvaluator:
    """在 composition root 使用的完整相关性评估装配函数。"""
    verifier = StructuredCandidateRelevanceClaimVerifier(
        settings,
        model_factory=partial(build_candidate_relevance_verification_model, settings),
    )
    return CandidateRelevanceEvaluator(
        settings,
        model_factory=partial(build_candidate_relevance_model, settings),
        claim_verifier=verifier,
    )
