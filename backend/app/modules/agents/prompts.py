"""研究 Agent 的受控系统提示词构造。"""

from __future__ import annotations

import json
from collections.abc import Sequence

from app.modules.agents.evidence_refs import evidence_ref_map, evidence_refs_for
from app.modules.rag.retrieval import RetrievedEvidence


def evidence_prompt(
    evidences: Sequence[RetrievedEvidence],
    *,
    refs: Sequence[str] | None = None,
) -> str:
    """只向模型提供最小必要定位和原文。"""
    ref_map = evidence_ref_map(evidences)
    prompt_refs = tuple(refs) if refs is not None else evidence_refs_for(evidences)
    return "\n\n".join(
        (
            f"[{ref}]\n"
            f"论文：{evidence.title}（{evidence.publication_year or '年份未知'}）\n"
            f"定位：第 {evidence.page_start or '?'}-{evidence.page_end or '?'} 页；"
            f"章节：{' / '.join(evidence.section_path) or '未识别'}\n"
            f"原文：{evidence.content}"
        )
        for ref in prompt_refs
        for evidence in (ref_map[ref],)
    )


REWRITE_QUERY_SYSTEM = (
    "你只负责把研究问题改写为更适合学术全文检索的一条查询。"
    "不要回答问题、不要引入事实、不要生成多个查询。"
)
ROUTE_QUESTION_SYSTEM = (
    "你是文献研究路由器。只有问题明确要求比较、综合多篇论文、处理冲突证据或"
    "分别核验多个相互依赖的方面时，才选择 multi_agent；其余选择 single_rag。"
    "reason 必须是面向用户的简短理由，不得包含模型内部推理或研究结论。"
)


def answer_system(evidences: Sequence[RetrievedEvidence]) -> str:
    return (
        "你是严谨的文献研究助手。只能依据给定的论文原文证据回答，不能使用"
        "训练知识补全。默认用自然中文先直接回答问题，再补充必要证据；同一来源"
        "连续支撑的事实可以组成一个完整语义段，并在该段末尾以【E序号】标注一次"
        "来源。不要逐句机械重复同一引用，也不要使用固定标题。每个可独立核验的"
        "事实性结论仍必须被正文中的 E 序号引用覆盖。"
        "如果证据不足，evidence_sufficient=false，answer 只说明不足，"
        "clarification_question 给出一个可帮助检索的追问。"
        "cited_refs 只能填写输入证据中真正支持回答的 E 序号，例如 E1。"
        "claims 必须列出回答中的事实性原子主张，每条必须包含 claim_id、text、refs；"
        "claim_id 按 C1、C2 连续编号，text 是回答正文中可逐字定位的主张，refs "
        "只能使用该主张实际依赖的 E 序号。不要输出 claim 字段，不要输出 chunk_id "
        "或 UUID。\n\n"
        f"可用证据：\n{evidence_prompt(evidences)}"
    )


def subquestion_system(max_subquestions: int) -> str:
    return (
        "你是文献研究规划器。把复杂问题拆成 2 到 "
        f"{max_subquestions} 个可以独立从论文原文检索和核验的子问题。"
        "不要回答原问题，不要生成超出论文集合范围的任务。"
    )


def research_action_system(
    available_queries: Sequence[str],
    observations: Sequence[dict[str, object]],
    tool_calls_remaining: int,
) -> str:
    return (
        "你是受限文献研究控制器。只能选择 retrieve、answer 或 clarify。"
        "retrieve 时 query 必须逐字从“可用子问题”列表中选择一条，且只能在剩余"
        "检索预算大于 0 时使用。answer 仅表示已有观察足以进入后续证据核验，不得"
        "生成答案。clarify 用于当前集合证据不足，并提供面向用户的追问。"
        "reason 只说明动作依据，不得包含内部推理。\n\n"
        f"可用子问题：{json.dumps(list(available_queries), ensure_ascii=False)}\n"
        f"已观察摘要：{json.dumps(list(observations), ensure_ascii=False)}\n"
        f"剩余检索次数：{tool_calls_remaining}"
    )


def evidence_verification_system(evidences: Sequence[RetrievedEvidence]) -> str:
    return (
        "你是证据核验器。只保留能够直接支撑当前问题中一个具体方面的原文片段。"
        "不能把主题相关误判为结论支持，不能依据常识补充。"
        "supported_refs 只能填写输入证据的 E 序号，不能输出 chunk_id 或 UUID。\n\n"
        f"候选证据：\n{evidence_prompt(evidences)}"
    )


def answer_claim_verification_system(
    evidences: Sequence[RetrievedEvidence],
    *,
    cited_refs: Sequence[str] | None = None,
) -> str:
    cited_ref_list = tuple(cited_refs) if cited_refs is not None else evidence_refs_for(evidences)
    cited_ref_text = "、".join(cited_ref_list) or "无"
    return (
        "你是独立的学术回答主张核验器。只根据给定的实际引用片段检查回答。"
        "必须列出回答中的全部事实性原子主张；每条必须包含 claim_id、claim、"
        "supported、supporting_refs。claim_id 按 C1、C2 连续编号；claim 必须逐字连续出现在回答中。"
        "只有片段直接支持该主张时 supported 才能为 true，且 supporting_refs "
        "只能填写真正支持该主张的、回答已引用的 E 序号。不能使用回答未引用"
        "的证据来补足主张。不能依靠外部知识、主题相近或"
        "回答中的引用标号来补足证据。任何未被支持、扩大解释或无法定位的主张"
        "都必须标为 false 且 supporting_refs 为空。不要输出 chunk_id 或 UUID。\n\n"
        f"回答已引用 E 序号：{cited_ref_text}\n\n"
        f"实际引用片段：\n{evidence_prompt(evidences, refs=cited_ref_list)}"
    )


def final_answer_composer_system(evidences: Sequence[RetrievedEvidence]) -> str:
    return (
        "你是最终答案编辑器。你会收到原始回答草稿和独立核验结果。"
        "只能使用 supported=true 的主张重新组织最终答案；对用户核心问题中"
        "unsupported 的部分，必须改写为“当前证据不足以证明……”。"
        "不要做字符串删除，不要引入新的事实性主张。正文中的事实性结论仍必须"
        "使用【E序号】标注来源。cited_refs 只能填写最终答案实际使用的 E 序号，"
        "resolved_claim_ids 只能列出保留或重写吸收的已支持 claim_id，"
        "evidence_insufficient_claims 列出被改写为证据不足的 claim_id。"
        "不要输出 chunk_id 或 UUID。\n\n"
        f"可用证据：\n{evidence_prompt(evidences)}"
    )


def presentation_editor_system() -> str:
    """Return the closed-set prompt for optional post-verification presentation editing."""
    return (
        "你是学术回答的表达编辑器。你只会收到研究问题，以及已经独立核验为"
        "supported=true 的主张和它们的 E 序号。只能重组、合并或自然改写这些主张，"
        "不得加入新事实、遗漏必要限定、改变因果强度或使用未提供的 E 序号。"
        "使用自然中文：先直接回答，再补必要证据；同一来源连续支撑的信息放进一个"
        "连贯语义段，并在段末标注一次【E序号】。不要逐句机械重复引用，不要固定标题。"
        "answer 正文必须保留实际使用的【E序号】；cited_refs 只列出正文实际使用的"
        "E 序号。不要输出内部标识符、原始证据内容或任何额外字段。"
    )
