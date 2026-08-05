"""研究 Agent 的受控系统提示词构造。"""

from __future__ import annotations

import json
from collections.abc import Sequence

from app.modules.rag.retrieval import RetrievedEvidence


def evidence_prompt(evidences: Sequence[RetrievedEvidence]) -> str:
    """只向模型提供最小必要定位和原文。"""
    return "\n\n".join(
        (
            f"[E{index}] chunk_id={evidence.chunk_id}\n"
            f"论文：{evidence.title}（{evidence.publication_year or '年份未知'}）\n"
            f"定位：第 {evidence.page_start or '?'}-{evidence.page_end or '?'} 页；"
            f"章节：{' / '.join(evidence.section_path) or '未识别'}\n"
            f"原文：{evidence.content}"
        )
        for index, evidence in enumerate(evidences, start=1)
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
        "训练知识补全。正文中每个事实性结论后必须以【E序号】标注来源。"
        "如果证据不足，evidence_sufficient=false，answer 只说明不足，"
        "clarification_question 给出一个可帮助检索的追问。"
        "cited_chunk_ids 只能填写输入证据中真正支持回答的 chunk_id。\n\n"
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
        "不能把主题相关误判为结论支持，不能依据常识补充。\n\n"
        f"候选证据：\n{evidence_prompt(evidences)}"
    )


def answer_claim_verification_system(evidences: Sequence[RetrievedEvidence]) -> str:
    return (
        "你是独立的学术回答主张核验器。只根据给定的实际引用片段检查回答。"
        "必须列出回答中的全部事实性原子主张；claim 必须逐字连续出现在回答中。"
        "只有片段直接支持该主张时 supported 才能为 true，且 supporting_chunk_ids "
        "只能填写真正支持该主张的输入 chunk_id。不能依靠外部知识、主题相近或"
        "回答中的引用标号来补足证据。任何未被支持、扩大解释或无法定位的主张"
        "都必须标为 false 且 supporting_chunk_ids 为空。\n\n"
        f"实际引用片段：\n{evidence_prompt(evidences)}"
    )
