"""基于统一候选元数据的受限相关性评估。

该模块只解释“候选为什么可能帮助当前研究”，不判断 DOI、题录、全文权限或
论文质量。模型不能改写统一候选本身，服务端会核验它引用的标题/摘要原文。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast
from uuid import UUID

from app.modules.search.contracts import (
    CandidateRelevanceAssessment,
    CandidateRelevanceError,
    CandidateRelevanceEvidence,
    CandidateRelevanceLevel,
    CandidateRelevanceState,
    UnifiedCandidate,
)
from app.modules.workflow.contracts import ProviderSearchQuery, ResearchScope
from app.modules.workflow.settings import WorkflowSettings
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)


class StructuredRelevanceModel(Protocol):
    """相关性模型的最小边界；流接口在运行时探测以兼容旧测试替身。"""

    async def ainvoke(self, input: list[SystemMessage | HumanMessage]) -> object:
        """仅兼容既有测试替身；生产实现不走此路径。"""
        raise NotImplementedError


class CandidateRelevanceStreamIdleTimeout(RuntimeError):
    """模型流在配置的活动空闲窗口内没有返回任何块。"""


class CandidateRelevanceStreamPayloadInvalid(ValueError):
    """流结束后没有形成可验证的完整 JSON，异常文本不包含模型正文。"""


class CandidateRelevanceCancelled(RuntimeError):
    """用户在完整 JSON 返回前显式取消了当前相关性运行。"""


def _stream_chunk_text(chunk: object) -> str:
    """提取 LangChain 消息块中的文本内容，不把元数据或推理字段写入结果。"""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    fragments: list[str] = []
    for item in content:
        if isinstance(item, str):
            fragments.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            fragments.append(item["text"])
    return "".join(fragments)


async def collect_streamed_json_object(
    model: StructuredRelevanceModel,
    messages: list[SystemMessage | HumanMessage],
    *,
    idle_timeout_seconds: float,
    cancellation_check: Callable[[], Awaitable[bool]] | None = None,
) -> object:
    """收集完整 JSON mode 流，仅以相邻流块间隔判断超时。

    每次 ``anext`` 都单独应用空闲窗口，因此模型可以持续运行任意长时间。测试
    替身没有 ``astream`` 时保留一次 ``ainvoke`` 兼容，真实 ``ChatOpenAI`` 绝不走
    该分支。
    """
    stream = getattr(model, "astream", None)
    if not callable(stream):
        return await model.ainvoke(messages)

    if cancellation_check is not None and await cancellation_check():
        raise CandidateRelevanceCancelled("候选相关性分析已取消。")

    iterator = cast(AsyncIterator[object], stream(messages)).__aiter__()
    fragments: list[str] = []
    last_cancellation_check = asyncio.get_running_loop().time()
    while True:
        now = asyncio.get_running_loop().time()
        if (
            cancellation_check is not None
            and now - last_cancellation_check >= 1.0
            and await cancellation_check()
        ):
            raise CandidateRelevanceCancelled("候选相关性分析已取消。")
        last_cancellation_check = now
        next_chunk = asyncio.ensure_future(anext(iterator))
        wait_started_at = asyncio.get_running_loop().time()
        try:
            while True:
                elapsed = asyncio.get_running_loop().time() - wait_started_at
                remaining = idle_timeout_seconds - elapsed
                if remaining <= 0:
                    next_chunk.cancel()
                    await asyncio.gather(next_chunk, return_exceptions=True)
                    raise CandidateRelevanceStreamIdleTimeout(
                        f"候选相关性模型连续 {idle_timeout_seconds:g} 秒没有流活动。"
                    )
                done, _pending = await asyncio.wait(
                    {next_chunk},
                    timeout=min(1.0, remaining),
                )
                if done:
                    chunk = next_chunk.result()
                    break
                if cancellation_check is not None and await cancellation_check():
                    next_chunk.cancel()
                    await asyncio.gather(next_chunk, return_exceptions=True)
                    raise CandidateRelevanceCancelled("候选相关性分析已取消。")
                last_cancellation_check = asyncio.get_running_loop().time()
                if next_chunk.done():
                    chunk = next_chunk.result()
                    break
        except StopAsyncIteration:
            break
        except CandidateRelevanceCancelled:
            raise
        fragments.append(_stream_chunk_text(chunk))

    payload = "".join(fragments).strip()
    if not payload:
        raise CandidateRelevanceStreamPayloadInvalid("候选相关性模型流没有返回 JSON 内容。")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CandidateRelevanceStreamPayloadInvalid("候选相关性模型流未形成完整 JSON。") from exc


@dataclass(frozen=True, slots=True)
class CandidateRelevanceClaimVerificationFailure:
    """独立理由核验拒绝或无法完成时的单候选失败摘要。"""

    code: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class CandidateRelevanceClaimVerificationResult:
    """独立理由核验的逐候选结果，不允许未核验评估继续展示。"""

    verified_candidate_ids: frozenset[UUID]
    failures: Mapping[UUID, CandidateRelevanceClaimVerificationFailure]


class CandidateRelevanceClaimVerifier(Protocol):
    """验证候选理由中的主张是否确实由标题、摘要和研究上下文支撑。"""

    async def verify(
        self,
        *,
        context: CandidateRelevanceContext,
        candidates: Sequence[UnifiedCandidate],
        assessments: Mapping[UUID, CandidateRelevanceAssessment],
        cancellation_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> CandidateRelevanceClaimVerificationResult:
        """返回已独立核验的候选与每条不可展示的失败原因。"""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class CandidateRelevanceContext:
    """一次已确认检索可安全发送给模型的研究上下文。"""

    research_question: str
    direction_title: str
    direction_summary: str
    subtopics: tuple[str, ...]
    search_queries: tuple[str, ...]
    start_year: int | None
    end_year: int | None
    languages: tuple[str, ...]


def build_candidate_relevance_context(
    *,
    research_question: str,
    direction_options: Sequence[object],
    selected_direction_id: str | None,
    query_specs: Sequence[ProviderSearchQuery],
    scope: ResearchScope,
) -> CandidateRelevanceContext:
    """从已确认计划提取 Agent 唯一需要的研究上下文。"""
    direction = next(
        (
            item
            for item in direction_options
            if isinstance(item, dict) and item.get("id") == selected_direction_id
        ),
        {},
    )
    return CandidateRelevanceContext(
        research_question=research_question,
        direction_title=str(direction.get("title", "当前研究方向")),
        direction_summary=str(direction.get("summary", "")),
        subtopics=tuple(str(item) for item in direction.get("subtopics", [])),
        search_queries=tuple(spec.query for spec in query_specs),
        start_year=scope.start_year,
        end_year=scope.end_year,
        languages=tuple(language.value for language in scope.languages),
    )


class CandidateRelevanceModelEvidence(BaseModel):
    """模型传输层的证据格式，来源字段由服务端优先确定。"""

    quote: str = Field(min_length=1, max_length=500)
    source_field: str | None = None


class CandidateRelevanceItem(BaseModel):
    """模型输出中针对一个统一候选的扁平评估格式。

    DeepSeek 的 JSON mode 能稳定返回扁平对象，但不可靠地遵循嵌套的
    ``assessment`` 包装。该传输格式只存在于模型边界；服务端会立即转换为
    ``CandidateRelevanceAssessment``，不会改变 API 或 Redis 快照契约。
    """

    candidate_id: UUID
    level: CandidateRelevanceLevel
    study_focus: str = Field(min_length=1, max_length=600)
    reason: str = Field(min_length=1, max_length=800)
    helpful_aspect: str = Field(min_length=1, max_length=800)
    limitations: tuple[str, ...] = Field(default_factory=tuple, max_length=4)
    recommendation: str = Field(min_length=1, max_length=500)
    evidence: tuple[CandidateRelevanceModelEvidence, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="before")
    @classmethod
    def normalize_unambiguous_json_shapes(cls, value: object) -> object:
        """兼容 JSON mode 的单项数组简写，不接受缺失或不可验证的证据。"""
        if not isinstance(value, dict):
            return value

        normalized = dict(value)
        if isinstance(normalized.get("limitations"), str):
            normalized["limitations"] = [normalized["limitations"]]
        if isinstance(normalized.get("evidence"), dict):
            normalized["evidence"] = [normalized["evidence"]]
        return normalized

    def to_assessment(self, candidate: UnifiedCandidate) -> CandidateRelevanceAssessment:
        """将模型传输对象转换为服务端可验证的业务评估。"""

        def evidence_source(item: CandidateRelevanceModelEvidence) -> Literal["title", "abstract"]:
            """只接受模型给出的合法来源字段，其余情况由候选原文确定。"""
            if item.source_field == "title":
                return "title"
            if item.source_field == "abstract":
                return "abstract"
            return _resolve_evidence_source(candidate, item.quote)

        evidence = tuple(
            CandidateRelevanceEvidence(
                source_field=evidence_source(item),
                quote=item.quote,
            )
            for item in self.evidence
        )
        return CandidateRelevanceAssessment(
            level=self.level,
            study_focus=self.study_focus,
            reason=self.reason,
            helpful_aspect=self.helpful_aspect,
            limitations=self.limitations,
            recommendation=self.recommendation,
            evidence=evidence,
        )


class CandidateRelevanceBatch(BaseModel):
    """单次批量模型调用的完整、可校验结果。"""

    assessments: tuple[CandidateRelevanceItem, ...] = Field(min_length=1)


_CLAIM_FIELD = Literal[
    "level",
    "study_focus",
    "reason",
    "helpful_aspect",
    "limitations",
    "recommendation",
]


class CandidateRelevanceClaimVerificationItem(BaseModel):
    """独立核验器对单条候选理由的结论。

    不让该节点改写理由内容；它只能确认整条理由的每个可见主张均有标题/摘要
    依据，或指出其中至少一个不能支持的字段。
    """

    candidate_id: UUID
    supported: bool
    unsupported_fields: tuple[_CLAIM_FIELD, ...] = Field(default_factory=tuple, max_length=6)

    @model_validator(mode="after")
    def support_flag_matches_fields(self) -> CandidateRelevanceClaimVerificationItem:
        """避免模型用空字段把无法判断的理由伪装成通过。"""
        if self.supported and self.unsupported_fields:
            raise ValueError("通过的理由核验不能携带不支持字段")
        if not self.supported and not self.unsupported_fields:
            raise ValueError("拒绝的理由核验必须指出至少一个不支持字段")
        return self


class CandidateRelevanceClaimVerificationBatch(BaseModel):
    """独立模型对完整、已通过引文存在性检查的候选集合的输出。"""

    verifications: tuple[CandidateRelevanceClaimVerificationItem, ...] = Field(min_length=1)


_SYSTEM_PROMPT = """你是学术文献候选相关性评估器。
只根据提供的研究上下文和每条候选的标题、摘要、年份、类型与载体判断相关性。
不得使用外部知识，不得声称读过全文，不得判断期刊等级或论文真实结论。

输出必须是一个 JSON 对象，形状严格为：
{"assessments":[{"candidate_id":"候选 UUID","level":"core","study_focus":"...",
"reason":"...","helpful_aspect":"...","limitations":["..."],"recommendation":"...",
"evidence":[{"quote":"候选标题或摘要中的逐字原文"}]}]}。
不要额外嵌套 assessment 字段。每个具备摘要的 candidate_id 恰好输出一次。字段要求：
1. level 只能为 core、related、background、not_recommended、insufficient_information。
2. study_focus、reason、helpful_aspect、limitations、recommendation 使用简洁中文；
   limitations 必须是字符串数组。
   study_focus 只概述这篇候选主要研究的对象、关系或方法，不能复述整段摘要。
3. 所有候选特定表述都必须收敛到标题或摘要明确写出的对象、关系、方法或结果：
   - 不得把“关联”说成因果、保护作用、机制、验证或确定结论；
   - 不得补充摘要未说明的样本范围、研究质量、发表偏倚、混杂、外推性或定量结果；
   - limitations 默认输出 []；只有标题或摘要明确写出限制时才可填写，并且不得使用
     研究设计的通用推断替代原文；
   - recommendation 只能给出中性的下一步动作，例如“建议进一步核对全文，不据此确认结论”，
     不能声称该候选能够支撑研究框架、假设、机制、比较或结论；
   - helpful_aspect 只能说明候选中已明确出现的内容与当前问题的对应关系，不能新增作用或结果。
4. evidence 的 quote 必须逐字摘自该候选给出的 title 或 abstract，不能改写、翻译或编造。
5. abstract 缺失的候选会由服务端确定性标为 insufficient_information；它们仍在输入中，
   用于帮助比较完整候选集合，但不要求在输出中重复生成判断。
6. 相关性不是质量分数，也不能承诺该候选可以进入 RAG。
"""


_CLAIM_VERIFICATION_SYSTEM_PROMPT = """你是独立的学术候选理由核验器。
你会收到研究上下文、每条候选的标题和摘要、以及先前生成的相关性理由和逐字证据。
只能判断这些可见主张是否被同一候选的标题或摘要直接支撑，不得使用外部知识、常识、
全文内容或论文题目之外的推断。

输出必须是一个 JSON 对象，形状严格为：
{"verifications":[{"candidate_id":"候选 UUID","supported":true,"unsupported_fields":[]}]}。
每个 candidate_id 恰好输出一次。要求：
1. 只有 level、study_focus、reason、helpful_aspect、limitations、recommendation 的所有可见主张
   都没有夸大研究对象、关系、方法、结果或可用性时，supported 才能为 true。
2. 若任何字段无法由标题或摘要直接支持，supported 必须为 false，并在 unsupported_fields 中
   列出一个或多个字段名。字段名只能使用 level、study_focus、reason、helpful_aspect、
   limitations、recommendation。
3. 不得改写原理由、不得新增候选、不得把“主题相近”误判为“研究结论得到支持”。
"""


class OpenAICompatibleCandidateRelevanceClaimVerifier:
    """对候选理由执行独立结构化主张核验，失败时默认拒绝展示。"""

    def __init__(
        self,
        settings: WorkflowSettings,
        *,
        model: StructuredRelevanceModel | None = None,
    ) -> None:
        self._settings = settings
        self._model = model

    async def verify(
        self,
        *,
        context: CandidateRelevanceContext,
        candidates: Sequence[UnifiedCandidate],
        assessments: Mapping[UUID, CandidateRelevanceAssessment],
        cancellation_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> CandidateRelevanceClaimVerificationResult:
        """完整核验候选集合；不可解析或遗漏的单项均不能继续显示理由。"""
        if not candidates:
            return CandidateRelevanceClaimVerificationResult(frozenset(), {})
        try:
            model = self._model or self._create_model(
                self._settings,
                candidate_count=len(candidates),
            )
            raw_result = await collect_streamed_json_object(
                model,
                [
                    SystemMessage(_CLAIM_VERIFICATION_SYSTEM_PROMPT),
                    HumanMessage(self._build_payload(context, candidates, assessments)),
                ],
                idle_timeout_seconds=self._settings.workflow_relevance_stream_idle_timeout_seconds,
                cancellation_check=cancellation_check,
            )
            result = CandidateRelevanceClaimVerificationBatch.model_validate(raw_result)
        except CandidateRelevanceCancelled:
            raise
        except CandidateRelevanceStreamIdleTimeout:
            logger.exception(
                "Candidate relevance claim verification stream became idle: candidate_count=%s",
                len(candidates),
            )
            return CandidateRelevanceClaimVerificationResult(
                frozenset(),
                {
                    candidate.candidate_id: CandidateRelevanceClaimVerificationFailure(
                        code="candidate_relevance_claim_verification_stream_idle_timeout",
                        message="候选理由的独立核验长时间没有返回活动，请稍后重新分析。",
                        retryable=True,
                    )
                    for candidate in candidates
                },
            )
        except CandidateRelevanceStreamPayloadInvalid:
            return CandidateRelevanceClaimVerificationResult(
                frozenset(),
                {
                    candidate.candidate_id: CandidateRelevanceClaimVerificationFailure(
                        code="candidate_relevance_claim_verification_invalid",
                        message="候选理由的独立核验返回格式无效，无法展示未经核验的结论。",
                        retryable=True,
                    )
                    for candidate in candidates
                },
            )
        except ValidationError:
            return CandidateRelevanceClaimVerificationResult(
                frozenset(),
                {
                    candidate.candidate_id: CandidateRelevanceClaimVerificationFailure(
                        code="candidate_relevance_claim_verification_invalid",
                        message="候选理由的独立核验返回格式无效，无法展示未经核验的结论。",
                        retryable=True,
                    )
                    for candidate in candidates
                },
            )
        except Exception:
            logger.exception(
                "Candidate relevance claim verification model call failed: candidate_count=%s",
                len(candidates),
            )
            return CandidateRelevanceClaimVerificationResult(
                frozenset(),
                {
                    candidate.candidate_id: CandidateRelevanceClaimVerificationFailure(
                        code="candidate_relevance_claim_verification_unavailable",
                        message="候选理由暂时无法完成独立证据核验，请稍后重试。",
                        retryable=True,
                    )
                    for candidate in candidates
                },
            )

        expected_ids = {candidate.candidate_id for candidate in candidates}
        grouped: dict[UUID, list[CandidateRelevanceClaimVerificationItem]] = {}
        for item in result.verifications:
            if item.candidate_id in expected_ids:
                grouped.setdefault(item.candidate_id, []).append(item)

        verified: set[UUID] = set()
        failures: dict[UUID, CandidateRelevanceClaimVerificationFailure] = {}
        for candidate in candidates:
            items = grouped.get(candidate.candidate_id, [])
            if not items:
                failures[candidate.candidate_id] = CandidateRelevanceClaimVerificationFailure(
                    code="candidate_relevance_claim_verification_invalid",
                    message="独立核验没有返回这条候选理由，不能展示未经核验的结论。",
                    retryable=True,
                )
                continue
            if len(items) != 1:
                failures[candidate.candidate_id] = CandidateRelevanceClaimVerificationFailure(
                    code="candidate_relevance_claim_verification_invalid",
                    message="独立核验为同一候选返回了重复结果，不能展示理由。",
                    retryable=True,
                )
                continue
            item = items[0]
            if item.supported:
                verified.add(candidate.candidate_id)
                continue
            fields = "、".join(item.unsupported_fields)
            failures[candidate.candidate_id] = CandidateRelevanceClaimVerificationFailure(
                code="candidate_relevance_claim_unsupported",
                message=f"候选理由中的 {fields} 无法由标题或摘要直接支持，已拒绝展示。",
                retryable=True,
            )
        return CandidateRelevanceClaimVerificationResult(frozenset(verified), failures)

    def _create_model(
        self,
        settings: WorkflowSettings,
        *,
        candidate_count: int,
    ) -> StructuredRelevanceModel:
        """为二次核验创建独立的结构化模型实例，避免复用第一次输出。"""
        max_tokens = (
            settings.workflow_relevance_verification_output_tokens_per_candidate * candidate_count
        )
        chat_model = ChatOpenAI(
            model=settings.active_chat_model,
            api_key=settings.active_api_key,
            base_url=settings.active_base_url,
            temperature=0,
            max_retries=0,
            max_tokens=max_tokens,  # pyright: ignore[reportCallIssue]
        )
        return chat_model.bind(response_format={"type": "json_object"})  # type: ignore[return-value]

    @staticmethod
    def _build_payload(
        context: CandidateRelevanceContext,
        candidates: Sequence[UnifiedCandidate],
        assessments: Mapping[UUID, CandidateRelevanceAssessment],
    ) -> str:
        """仅发送核验理由及其允许使用的标题/摘要，不传下载或来源原始字段。"""
        return json.dumps(
            {
                "research_context": {
                    "question": context.research_question,
                    "direction_title": context.direction_title,
                    "direction_summary": context.direction_summary,
                    "subtopics": context.subtopics,
                },
                "candidates": [
                    {
                        "candidate_id": str(candidate.candidate_id),
                        "title": candidate.title,
                        "abstract": candidate.abstract,
                        "assessment": {
                            "level": assessment.level,
                            "study_focus": assessment.study_focus,
                            "reason": assessment.reason,
                            "helpful_aspect": assessment.helpful_aspect,
                            "limitations": assessment.limitations,
                            "recommendation": assessment.recommendation,
                            "evidence": [item.model_dump() for item in assessment.evidence],
                        },
                    }
                    for candidate in candidates
                    if (assessment := assessments.get(candidate.candidate_id)) is not None
                ],
            },
            ensure_ascii=False,
        )


class OpenAICompatibleCandidateRelevanceEvaluator:
    """使用现有 OpenAI 兼容聊天模型评估统一候选的语义关系。"""

    def __init__(
        self,
        settings: WorkflowSettings,
        *,
        model: StructuredRelevanceModel | None = None,
        claim_verifier: CandidateRelevanceClaimVerifier | None = None,
    ) -> None:
        self._settings = settings
        self._model = model
        self._claim_verifier = claim_verifier or OpenAICompatibleCandidateRelevanceClaimVerifier(
            settings
        )

    async def assess(
        self,
        *,
        context: CandidateRelevanceContext,
        candidates: Sequence[UnifiedCandidate],
        cancellation_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> tuple[UnifiedCandidate, ...]:
        """批量评估已经规整的候选，并把验证后的结果附回原对象。"""
        if not candidates:
            return ()

        candidates_with_abstract = [candidate for candidate in candidates if candidate.abstract]
        without_abstract = [candidate for candidate in candidates if not candidate.abstract]
        assessed_candidates: dict[UUID, UnifiedCandidate] = {
            candidate.candidate_id: self._insufficient_candidate(candidate)
            for candidate in without_abstract
        }
        if candidates_with_abstract:
            try:
                model = self._model or self._create_model(
                    self._settings,
                    candidate_count=len(candidates_with_abstract),
                )
                raw_result = await collect_streamed_json_object(
                    model,
                    [
                        SystemMessage(_SYSTEM_PROMPT),
                        HumanMessage(self._build_payload(context, candidates)),
                    ],
                    idle_timeout_seconds=self._settings.workflow_relevance_stream_idle_timeout_seconds,
                    cancellation_check=cancellation_check,
                )
                result = CandidateRelevanceBatch.model_validate(raw_result)
            except CandidateRelevanceCancelled:
                raise
            except CandidateRelevanceStreamIdleTimeout:
                logger.exception(
                    "Candidate relevance stream became idle: candidate_count=%s",
                    len(candidates_with_abstract),
                )
                assessed_candidates.update(
                    {
                        candidate.candidate_id: mark_candidate_relevance_failed(
                            candidate,
                            "候选相关性分析长时间没有返回活动，请稍后重新分析。",
                            code="candidate_relevance_stream_idle_timeout",
                        )
                        for candidate in candidates_with_abstract
                    }
                )
            except CandidateRelevanceStreamPayloadInvalid:
                assessed_candidates.update(
                    {
                        candidate.candidate_id: mark_candidate_relevance_failed(
                            candidate,
                            "候选相关性模型返回的数据不符合约定，无法生成可靠理由。",
                            code="candidate_relevance_output_invalid",
                        )
                        for candidate in candidates_with_abstract
                    }
                )
            except ValidationError:
                assessed_candidates.update(
                    {
                        candidate.candidate_id: mark_candidate_relevance_failed(
                            candidate,
                            "候选相关性模型返回的数据不符合约定，无法生成可靠理由。",
                            code="candidate_relevance_output_invalid",
                        )
                        for candidate in candidates_with_abstract
                    }
                )
            except Exception:
                logger.exception(
                    "Candidate relevance model call failed: candidate_count=%s",
                    len(candidates_with_abstract),
                )
                assessed_candidates.update(
                    {
                        candidate.candidate_id: mark_candidate_relevance_failed(
                            candidate,
                            "候选相关性模型暂时不可用，请稍后重试。",
                            code="candidate_relevance_model_unavailable",
                        )
                        for candidate in candidates_with_abstract
                    }
                )
            else:
                assessments, errors = self._validate_batch(candidates_with_abstract, result)
                verification_kwargs = {
                    "context": context,
                    "candidates": candidates_with_abstract,
                    "assessments": assessments,
                }
                if cancellation_check is not None:
                    claim_verification = await self._claim_verifier.verify(
                        **verification_kwargs,
                        cancellation_check=cancellation_check,
                    )
                else:
                    claim_verification = await self._claim_verifier.verify(**verification_kwargs)
                for candidate in candidates_with_abstract:
                    assessment = assessments.get(candidate.candidate_id)
                    if assessment is None:
                        assessed_candidates[candidate.candidate_id] = (
                            mark_candidate_relevance_failed(
                                candidate,
                                errors[candidate.candidate_id],
                                code="candidate_relevance_output_invalid",
                            )
                        )
                        continue
                    verification_failure = claim_verification.failures.get(candidate.candidate_id)
                    if verification_failure is not None:
                        assessed_candidates[candidate.candidate_id] = (
                            mark_candidate_relevance_failed(
                                candidate,
                                verification_failure.message,
                                code=verification_failure.code,
                                retryable=verification_failure.retryable,
                            )
                        )
                        continue
                    if candidate.candidate_id not in claim_verification.verified_candidate_ids:
                        assessed_candidates[candidate.candidate_id] = (
                            mark_candidate_relevance_failed(
                                candidate,
                                "候选理由未通过独立证据核验，不能展示为确定结论。",
                                code="candidate_relevance_claim_verification_invalid",
                            )
                        )
                        continue
                    assessed_candidates[candidate.candidate_id] = candidate.model_copy(
                        update={
                            "relevance_state": CandidateRelevanceState.COMPLETED,
                            "relevance_assessment": assessment,
                            "relevance_error": None,
                        }
                    )
        return tuple(assessed_candidates[candidate.candidate_id] for candidate in candidates)

    @staticmethod
    def _create_model(
        settings: WorkflowSettings,
        *,
        candidate_count: int,
    ) -> StructuredRelevanceModel:
        """创建 JSON 模式模型，具体证据真实性仍由本模块二次校验。"""
        chat_model = ChatOpenAI(
            model=settings.active_chat_model,
            api_key=settings.active_api_key,
            base_url=settings.active_base_url,
            temperature=0,
            max_retries=0,
            # 当前依赖版本的 Pyright 类型定义遗漏了运行时已支持的参数；DeepSeek 也使用它
            # 控制 completion 长度。预算随完整集合规模增加，不能以候选数阻断或拆批。
            max_tokens=(settings.workflow_relevance_output_tokens_per_candidate * candidate_count),  # pyright: ignore[reportCallIssue]
        )
        return chat_model.bind(response_format={"type": "json_object"})  # type: ignore[return-value]

    @staticmethod
    def _build_payload(
        context: CandidateRelevanceContext,
        candidates: Sequence[UnifiedCandidate],
    ) -> str:
        """只序列化相关性判断必要字段，避免把来源原始记录和下载链接送入模型。"""
        data = {
            "research_context": {
                "question": context.research_question,
                "direction": {
                    "title": context.direction_title,
                    "summary": context.direction_summary,
                    "subtopics": context.subtopics,
                },
                "scope": {
                    "start_year": context.start_year,
                    "end_year": context.end_year,
                    "languages": context.languages,
                },
                "search_queries": context.search_queries,
            },
            "candidates": [
                {
                    "candidate_id": str(candidate.candidate_id),
                    "title": candidate.title,
                    "abstract": candidate.abstract,
                    "authors": [author.name for author in candidate.authors[:8]],
                    "published_year": candidate.published_year,
                    "venue": candidate.venue,
                    "document_type": candidate.document_type,
                    "language": candidate.language.value,
                    "triage_warnings": [warning.value for warning in candidate.triage.warnings]
                    if candidate.triage
                    else [],
                }
                for candidate in candidates
            ],
        }
        return json.dumps(data, ensure_ascii=False)

    @staticmethod
    def _validate_batch(
        candidates: Sequence[UnifiedCandidate],
        result: CandidateRelevanceBatch,
    ) -> tuple[dict[UUID, CandidateRelevanceAssessment], dict[UUID, str]]:
        """逐条核验模型输出，坏记录不能抹掉同批已通过验证的结果。"""
        expected = {candidate.candidate_id: candidate for candidate in candidates}
        grouped: dict[UUID, list[CandidateRelevanceItem]] = {}
        for item in result.assessments:
            if item.candidate_id in expected:
                grouped.setdefault(item.candidate_id, []).append(item)

        valid: dict[UUID, CandidateRelevanceAssessment] = {}
        errors: dict[UUID, str] = {}
        for candidate_id, candidate in expected.items():
            items = grouped.get(candidate_id, [])
            if not items:
                errors[candidate_id] = "模型没有返回这条候选的相关性评估结果。"
                continue
            if len(items) != 1:
                errors[candidate_id] = "模型为同一候选返回了重复的相关性评估结果。"
                continue
            assessment = items[0].to_assessment(candidate)
            evidence_error = OpenAICompatibleCandidateRelevanceEvaluator._validate_evidence(
                candidate,
                assessment,
            )
            if evidence_error is not None:
                errors[candidate_id] = evidence_error
                continue
            valid[candidate_id] = assessment
        return valid, errors

    @staticmethod
    def _validate_evidence(
        candidate: UnifiedCandidate,
        assessment: CandidateRelevanceAssessment,
    ) -> str | None:
        """验证每条判断依据都能在同一候选的标题或摘要中找到。"""
        for evidence in assessment.evidence:
            source = candidate.title if evidence.source_field == "title" else candidate.abstract
            if source is None or _normalize(evidence.quote) not in _normalize(source):
                return "模型理由包含无法在候选标题或摘要中核对的依据。"
        return None

    @staticmethod
    def _insufficient_candidate(candidate: UnifiedCandidate) -> UnifiedCandidate:
        """无摘要时不调用模型，明确标注可判断信息不足的事实状态。"""
        return mark_candidate_relevance_insufficient(candidate)


def mark_candidate_relevance_failed(
    candidate: UnifiedCandidate,
    message: str,
    *,
    code: str,
    retryable: bool = True,
) -> UnifiedCandidate:
    """把模型或配置失败保存在单个候选上，绝不降级回关键词推测。"""
    return candidate.model_copy(
        update={
            "relevance_state": CandidateRelevanceState.FAILED,
            "relevance_assessment": None,
            "relevance_error": CandidateRelevanceError(
                code=code,
                message=message,
                retryable=retryable,
            ),
        }
    )


def mark_candidate_relevance_insufficient(candidate: UnifiedCandidate) -> UnifiedCandidate:
    """基于缺摘要这一确定事实生成“信息不足”，不让模型猜测。"""
    assessment = CandidateRelevanceAssessment(
        level=CandidateRelevanceLevel.INSUFFICIENT_INFORMATION,
        study_focus=(
            f"目前只能从题目确认它涉及“{candidate.title}”，缺少摘要，无法可靠概括研究内容。"
        ),
        reason="当前候选没有摘要，无法可靠判断它是否覆盖本研究的对象、关系或研究设计。",
        helpful_aspect="可先作为待核对题录保留，补充摘要或查看原文后再判断。",
        limitations=("缺少摘要，尚不能确认研究对象、方法和结论。",),
        recommendation="建议先补充摘要或打开原文，再决定是否优先获取全文。",
        evidence=(CandidateRelevanceEvidence(source_field="title", quote=candidate.title),),
    )
    return candidate.model_copy(
        update={
            "relevance_state": CandidateRelevanceState.COMPLETED,
            "relevance_assessment": assessment,
            "relevance_error": None,
        }
    )


def skip_candidate_relevance(candidate: UnifiedCandidate) -> UnifiedCandidate:
    """基础初筛已排除的候选不消耗模型调用，也不能永久停在“分析中”。"""
    return candidate.model_copy(
        update={
            "relevance_state": CandidateRelevanceState.SKIPPED,
            "relevance_assessment": None,
            "relevance_error": None,
        }
    )


def _normalize(value: str) -> str:
    """松弛处理空白和大小写后比较证据，仍不允许改变原文词语。"""
    return " ".join(value.casefold().split())


def _resolve_evidence_source(
    candidate: UnifiedCandidate,
    quote: str,
) -> Literal["title", "abstract"]:
    """从候选自身元数据确定证据字段；无法定位时选标题并由验证器拒绝。"""
    normalized_quote = _normalize(quote)
    if normalized_quote in _normalize(candidate.title):
        return "title"
    if candidate.abstract is not None and normalized_quote in _normalize(candidate.abstract):
        return "abstract"
    return "title"
