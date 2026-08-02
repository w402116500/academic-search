import type { Candidate, CandidateRelevanceLevel } from "@/api/types";

export type CandidateRelevanceTier =
  CandidateRelevanceLevel | "boundary" | "unavailable" | "skipped";

export interface CandidateRelevancePresentation {
  tier: CandidateRelevanceTier;
  tierLabel: string;
  relevanceSummary: string;
  studyFocus: string;
  helpfulAspect: string;
  limitations: string[];
  recommendation: string;
  evidence: Array<{ label: string; quote: string }>;
  evidenceBoundary: string;
  canRetry: boolean;
}

const LEVEL_LABELS: Record<CandidateRelevanceLevel, string> = {
  core: "核心相关",
  related: "关联研究",
  background: "背景参考",
  not_recommended: "不建议优先",
  insufficient_information: "信息不足",
};

/** 将服务端短期评估快照转换为列表和检查器共用的用户语言。 */
export function presentCandidateRelevance(candidate: Candidate): CandidateRelevancePresentation {
  const state = candidate.relevance_state ?? "pending";
  const assessment = candidate.relevance_assessment;
  if (state === "pending") {
    return unavailablePresentation(
      "正在分析",
      "系统正在根据标题和摘要判断这篇文献为什么值得优先查看。",
      "等待分析完成后，系统会给出简短说明和可核对的标题或摘要依据。",
    );
  }
  if (state === "skipped") {
    return {
      tier: "skipped",
      tierLabel: "未进入分析",
      relevanceSummary: "这条记录没有通过基础筛选，因此未投入模型分析。",
      studyFocus: "当前不为这条记录生成研究内容概述。",
      helpfulAspect: "它不作为当前研究的优先审核对象。",
      limitations: ["基础筛选未保留该记录，相关性 Agent 没有读取或判断它。"],
      recommendation: "如需进一步确认，可先查看完整题录和摘要。",
      evidence: [],
      evidenceBoundary: "该状态来自基础筛选，不是 Agent 对论文内容的结论。",
      canRetry: false,
    };
  }
  if (state === "failed" || !assessment) {
    return {
      ...unavailablePresentation(
        "分析未完成",
        candidate.relevance_error?.message ?? "当前无法生成可靠的候选理由。",
        "系统不会用关键词匹配替代这次失败的 Agent 判断。",
      ),
      canRetry: candidate.relevance_error?.retryable === true,
    };
  }

  return {
    tier: normalizeTier(assessment.level),
    tierLabel: LEVEL_LABELS[assessment.level],
    relevanceSummary: assessment.reason,
    studyFocus: assessment.study_focus,
    helpfulAspect: assessment.helpful_aspect,
    limitations: assessment.limitations,
    recommendation: assessment.recommendation,
    evidence: assessment.evidence.map((item) => ({
      label: item.source_field === "title" ? "标题依据" : "摘要依据",
      quote: item.quote,
    })),
    evidenceBoundary: "相关性判断只依据当前候选的标题和摘要，不代表系统已阅读全文。",
    canRetry: false,
  };
}

function unavailablePresentation(
  tierLabel: string,
  relevanceSummary: string,
  studyFocus: string,
): CandidateRelevancePresentation {
  return {
    tier: "unavailable",
    tierLabel,
    relevanceSummary,
    studyFocus,
    helpfulAspect: "等待可验证的评估结果后，再判断它对当前研究的具体帮助。",
    limitations: ["当前没有可核对的完整相关性判断。"],
    recommendation: "可稍后刷新，或查看题录和摘要后自行决定是否保留。",
    evidence: [],
    evidenceBoundary: "没有完成的 Agent 判断时，系统不会生成替代性理由。",
    canRetry: false,
  };
}

function normalizeTier(level: CandidateRelevanceLevel): CandidateRelevanceTier {
  if (level === "not_recommended") return "boundary";
  if (level === "insufficient_information") return "unavailable";
  return level;
}
