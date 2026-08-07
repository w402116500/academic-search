"""单轮 RAG 图节点。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from app.modules.agents.contracts import (
    AnswerClaimVerification,
    ResearchChatModel,
    ResearchModelError,
    ResearchModelProtocolError,
)
from app.modules.agents.evidence_refs import (
    canonical_answer_cited_refs,
    chunk_ids_for_refs,
    evidence_snapshot_trace,
    invalid_evidence_refs,
    render_user_citations,
)
from app.modules.agents.presentation import conditionally_edit_verified_answer
from app.modules.agents.state import SingleRagState, evidence_from_state, evidence_to_state
from app.modules.rag.retrieval import RetrievalResult, RetrievedEvidence
from app.modules.research.contracts import ResearchRunStage
from app.modules.research.execution_port import ResearchExecutionContext
from app.modules.research.settings import ResearchSettings

StageEmitter = Callable[[ResearchRunStage, str | None, int], Awaitable[None]]
ModelCaller = Callable[[Callable[[], Awaitable[Any]]], Awaitable[Any]]
RetrievalCaller = Callable[[object, str], Awaitable[RetrievalResult]]


class SingleRagNodes:
    """绑定单轮图所需的模型、检索和公开阶段回调。"""

    def __init__(
        self,
        *,
        model: ResearchChatModel,
        settings: ResearchSettings,
        emit: StageEmitter,
        call_model: ModelCaller,
        retrieve: RetrievalCaller,
    ) -> None:
        self._model = model
        self._settings = settings
        self._emit = emit
        self._call_model = call_model
        self._retrieve = retrieve

    def retrieve(
        self, context: ResearchExecutionContext
    ) -> Callable[[SingleRagState], Awaitable[dict[str, object]]]:
        """创建绑定不可变集合 scope 的节点，模型不会接触权限过滤参数。"""

        async def retrieve_node(state: SingleRagState) -> dict[str, object]:
            await self._emit(ResearchRunStage.HYBRID_RETRIEVAL, "正在检索当前集合中的原文证据。", 0)
            result = await self._retrieve(context.retrieval_scope, state["query"])
            await self._emit(
                ResearchRunStage.PARENT_MERGING,
                "正在补全同一论文中的相关上下文。",
                len(result.evidences),
            )
            return {
                "evidences": [evidence_to_state(item) for item in result.evidences],
                "retrieval_trace": dict(result.trace),
            }

        return retrieve_node

    async def assess(self, state: SingleRagState) -> dict[str, object]:
        """证据为空时最多允许一次改写，之后强制澄清。"""
        reranker = state["retrieval_trace"].get("reranker")
        reranker_enabled = isinstance(reranker, dict) and reranker.get("enabled") is True
        await self._emit(
            ResearchRunStage.RERANKING,
            "正在使用真实 Reranker 精排候选证据。"
            if reranker_enabled
            else "正在按 RRF 融合结果筛选证据；当前未启用真实 Reranker。",
            len(state["evidences"]),
        )
        if state["evidences"]:
            return {"route": "answer"}
        if state["rewrite_count"] < self._settings.rag_max_query_rewrites:
            return {"route": "rewrite"}
        return {"route": "clarify"}

    async def rewrite(self, state: SingleRagState) -> dict[str, object]:
        """查询改写只更新检索词，不能作为答案或引用进入最终结果。"""
        rewritten = await self._call_model(lambda: self._model.rewrite_query(state["question"]))
        return {"query": rewritten, "rewrite_count": state["rewrite_count"] + 1}

    async def answer(self, state: SingleRagState) -> dict[str, object]:
        """只把 RRF 入选证据传给回答模型。"""
        evidences = tuple(evidence_from_state(item) for item in state["evidences"])
        await self._emit(
            ResearchRunStage.ANSWERING,
            "正在依据已检索证据整理回答。",
            len(evidences),
        )
        answer = await self._call_model_with_snapshot(
            lambda: self._model.generate_answer(
                question=state["question"],
                evidences=evidences,
            ),
            evidences,
        )
        if not answer.evidence_sufficient:
            return {
                "route": "clarify",
                "clarification_question": answer.clarification_question
                or "当前证据不足以回答，请补充研究对象或限定条件。",
            }
        return {
            "route": "answer",
            "answer": answer.answer,
            "cited_refs": list(answer.cited_refs),
            "retrieval_trace": {
                **state["retrieval_trace"],
                "evidence_snapshot": evidence_snapshot_trace(evidences),
            },
        }

    async def verify_answer(self, state: SingleRagState) -> dict[str, object]:
        """保存回答前独立核验原子主张与实际引用片段。"""
        evidences = tuple(evidence_from_state(item) for item in state["evidences"])
        cited_refs = _validated_cited_refs_from_answer(
            answer=state["answer"],
            evidences=evidences,
            cited_refs=state["cited_refs"],
        )
        await self._emit(
            ResearchRunStage.EVIDENCE_VERIFYING,
            "正在逐项核验回答主张是否被实际引用的原文支持。",
            len(cited_refs),
        )
        verification = await self._call_model_with_snapshot(
            lambda: self._model.verify_answer_claims(
                question=state["question"],
                answer=state["answer"],
                evidences=evidences,
                cited_refs=cited_refs,
            ),
            evidences,
        )
        _ensure_supporting_refs_are_cited(verification, cited_refs, evidences)
        unsupported_count = sum(not item.supported for item in verification.claims)
        trace = {
            **state["retrieval_trace"],
            "answer_claim_verification": {
                "claim_count": len(verification.claims),
                "unsupported_claim_count": unsupported_count,
                "status": "supported" if unsupported_count == 0 else "unsupported",
                "claims": verification.model_dump(mode="json")["claims"],
                "repair_count": state["repair_count"],
            },
        }
        if unsupported_count:
            if state["repair_count"] == 0:
                return {
                    "route": "repair",
                    "answer_claim_verification": verification.model_dump(mode="json"),
                    "retrieval_trace": trace,
                }
            return {
                "route": "clarify",
                "clarification_question": (
                    "当前检索到的原文无法完整支持准备输出的结论。请缩小问题范围或补充相关文献。"
                ),
                "retrieval_trace": trace,
            }
        answer_to_render = state["answer"]
        if state["repair_count"] == 0:
            presentation = await conditionally_edit_verified_answer(
                model=self._model,
                call_model=self._call_model,
                question=state["question"],
                writer_answer=state["answer"],
                verification=verification,
                evidences=evidences,
                cited_refs=cited_refs,
            )
            answer_to_render = presentation.answer
            trace = {**trace, "presentation_quality": presentation.audit}
        try:
            rendered_answer, citations = render_user_citations(answer_to_render, evidences)
        except ValueError as exc:
            raise ResearchModelError("最终回答引用了不属于当前证据快照的标识。") from exc
        if not citations:
            raise ResearchModelError("最终回答没有可展示的证据引用。")
        cited_refs = [citation.evidence_ref for citation in citations]
        cited_chunk_ids = [str(chunk_id) for chunk_id in chunk_ids_for_refs(evidences, cited_refs)]
        return {
            "route": "answer",
            "answer": rendered_answer,
            "cited_refs": cited_refs,
            "cited_chunk_ids": cited_chunk_ids,
            "retrieval_trace": {
                **trace,
                "user_citations": [
                    {
                        "display_index": citation.display_index,
                        "evidence_ref": citation.evidence_ref,
                        "chunk_id": str(citation.chunk_id),
                    }
                    for citation in citations
                ],
            },
        }

    async def repair_answer(self, state: SingleRagState) -> dict[str, object]:
        """核验失败时重新组织答案，而不是机械删除原文片段。"""
        evidences = tuple(evidence_from_state(item) for item in state["evidences"])
        verification = AnswerClaimVerification.model_validate(state["answer_claim_verification"])
        await self._emit(
            ResearchRunStage.ANSWERING,
            "正在按已核验证据修复回答。",
            len(evidences),
        )
        try:
            final_answer = await self._compose_final_answer_once(state, verification, evidences)
            repair_protocol_attempts = 1
        except ResearchModelProtocolError:
            repair_protocol_attempts = 2
            await self._emit(
                ResearchRunStage.ANSWERING,
                "正在重新生成符合证据协议的修复回答。",
                len(evidences),
            )
            try:
                final_answer = await self._compose_final_answer_once(state, verification, evidences)
            except ResearchModelProtocolError:
                return {
                    "route": "clarify",
                    "clarification_question": (
                        "当前检索到的原文无法稳定生成可核验的修复回答。"
                        "请缩小问题范围或限定到具体论文后重试。"
                    ),
                    "retrieval_trace": {
                        **state["retrieval_trace"],
                        "answer_repair": {
                            "status": "fallback",
                            "fallback_reason": "protocol_error",
                            "protocol_attempts": repair_protocol_attempts,
                        },
                    },
                }
        return {
            "route": "answer",
            "answer": final_answer.answer,
            "cited_refs": list(final_answer.cited_refs),
            "repair_count": state["repair_count"] + 1,
            "retrieval_trace": {
                **state["retrieval_trace"],
                "answer_repair": {
                    "status": "completed",
                    "protocol_attempts": repair_protocol_attempts,
                },
            },
        }

    async def _compose_final_answer_once(
        self,
        state: SingleRagState,
        verification: AnswerClaimVerification,
        evidences: Sequence[RetrievedEvidence],
    ) -> Any:
        """调用一次最终答案编辑器；协议重试由调用方控制预算和次数。"""
        return await self._call_model_with_snapshot(
            lambda: self._model.compose_final_answer(
                question=state["question"],
                draft_answer=state["answer"],
                verification=verification,
                evidences=evidences,
            ),
            evidences,
        )

    async def _call_model_with_snapshot(
        self,
        operation: Callable[[], Awaitable[Any]],
        evidences: Sequence[RetrievedEvidence],
    ) -> Any:
        """Persist snapshot diagnostics when a model violates the RAG evidence contract."""
        try:
            return await self._call_model(operation)
        except ResearchModelProtocolError as exc:
            exc.add_evidence_snapshot(evidence_snapshot_trace(evidences))
            raise

    async def clarify(self, state: SingleRagState) -> dict[str, object]:
        """证据不足是正常终态，前端会显示澄清问题。"""
        await self._emit(
            ResearchRunStage.AWAITING_CLARIFICATION,
            "当前集合证据不足，需要补充问题。",
            0,
        )
        return {
            "clarification_question": state.get("clarification_question")
            or "当前研究集合没有足够证据支持这个问题。请补充研究对象、条件或限定到具体论文。"
        }


def _validated_cited_refs_from_answer(
    *,
    answer: str,
    evidences: Sequence[RetrievedEvidence],
    cited_refs: Sequence[str],
) -> tuple[str, ...]:
    try:
        return canonical_answer_cited_refs(answer, evidences, cited_refs)
    except ValueError as exc:
        error = ResearchModelProtocolError("回答正文引用无效，无法映射到证据快照。")
        error.add_evidence_snapshot(evidence_snapshot_trace(evidences))
        raise error from exc


def _ensure_supporting_refs_are_cited(
    verification: AnswerClaimVerification,
    cited_refs: Sequence[str],
    evidences: Sequence[RetrievedEvidence],
) -> None:
    allowed_refs = set(cited_refs)
    for item in verification.claims:
        invalid = invalid_evidence_refs(item.supporting_refs, allowed_refs)
        if invalid:
            error = ResearchModelProtocolError("回答主张核验器返回了未被回答实际引用的片段标识。")
            error.add_evidence_snapshot(evidence_snapshot_trace(evidences))
            raise error
