import type { Conversation, ResearchEvidence, ResearchRun } from "@/api/types";

export function conversationTitle(conversation: Conversation | null): string {
  return conversation?.title?.trim() || "新建研究对话";
}

function evidenceAuthorName(author: unknown): string {
  if (typeof author !== "object" || author === null) return "";
  const record = author as Record<string, unknown>;
  const name = typeof record.name === "string" ? record.name.trim() : "";
  if (name) return name;

  const literal = typeof record.literal === "string" ? record.literal.trim() : "";
  if (literal) return literal;

  return [record.given, record.family]
    .map((part) => (typeof part === "string" ? part.trim() : ""))
    .filter(Boolean)
    .join(" ");
}

export function evidenceAuthors(evidence: ResearchEvidence): string {
  const authors = evidence.authors.map(evidenceAuthorName).filter(Boolean);
  return authors.length ? authors.slice(0, 3).join("、") : "作者信息待补全";
}

export function evidenceLocation(evidence: ResearchEvidence): string {
  const locator = evidence.locator_snapshot ?? {};
  const pageStart = typeof locator.page_start === "number" ? locator.page_start : null;
  const pageEnd = typeof locator.page_end === "number" ? locator.page_end : null;
  const pages = pageStart
    ? pageEnd && pageEnd !== pageStart
      ? `第 ${pageStart}-${pageEnd} 页`
      : `第 ${pageStart} 页`
    : null;
  const sectionPath = Array.isArray(locator.section_path)
    ? locator.section_path.filter((item): item is string => typeof item === "string").join(" / ")
    : "";
  return [pages, sectionPath || null].filter(Boolean).join(" · ") || "原文定位已保存";
}

export function citedEvidences(run: ResearchRun | null): ResearchEvidence[] {
  return (run?.evidences ?? [])
    .filter((evidence) => evidence.is_cited)
    .sort((left, right) => {
      const leftIndex = left.display_index ?? Number.MAX_SAFE_INTEGER;
      const rightIndex = right.display_index ?? Number.MAX_SAFE_INTEGER;
      return leftIndex - rightIndex;
    });
}

export function candidateEvidences(run: ResearchRun | null): ResearchEvidence[] {
  if (!isStrictResearch(run)) return [];
  return (run?.evidences ?? []).filter((evidence) => !evidence.is_cited);
}

export function citedEvidenceIndexes(run: ResearchRun | null): number[] {
  return citedEvidences(run)
    .map((evidence) => evidence.display_index)
    .filter((index): index is number => typeof index === "number");
}

export function evidenceElementId(runId: string, evidenceId: string): string {
  return `research-evidence-${runId}-${evidenceId}`;
}

export function researchRunForOutputMessage(
  runs: ResearchRun[],
  messageId: string,
): ResearchRun | null {
  return runs.find((run) => run.output_message_id === messageId) ?? null;
}

export function rerankerDisabled(run: ResearchRun | null): boolean {
  const reranker = run?.retrieval_trace.reranker;
  return (
    typeof reranker === "object" &&
    reranker !== null &&
    "enabled" in reranker &&
    reranker.enabled === false
  );
}

export function cancellationRequested(run: ResearchRun | null): boolean {
  return run?.status === "running" && run.cancel_requested_at !== null;
}

function isTraceRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function researchExecutionModeLabel(run: ResearchRun | null): string | null {
  const trace = run?.retrieval_trace;
  if (!trace) return null;
  const executionMode = trace.execution_mode ?? trace.mode;
  if (executionMode === "fast_rag") return "快速问答";
  if (executionMode === "strict_research") return "深度研究";
  return null;
}

export function isStrictResearch(run: ResearchRun | null): boolean {
  const trace = run?.retrieval_trace;
  return trace?.execution_mode === "strict_research" || trace?.mode === "strict_research";
}

export function isEvidenceInsufficientRun(run: ResearchRun | null): boolean {
  return run?.status === "awaiting_clarification" && citedEvidences(run).length === 0;
}

export function citationAuditLabel(run: ResearchRun | null): string | null {
  const citationCount = citedEvidences(run).length;
  if (!citationCount) return null;
  const claimVerified = run?.retrieval_trace.claim_verified;
  if (isStrictResearch(run) && claimVerified === true) {
    return `${citationCount} 条引用与主张已核验`;
  }
  const citationChecked = run?.retrieval_trace.citation_checked;
  if (citationChecked === true) return `${citationCount} 条引用已检查`;
  return `${citationCount} 条引用`;
}

export function governanceSummary(run: ResearchRun | null): string | null {
  const trace = run?.retrieval_trace;
  if (!trace) return null;
  const routing = trace.routing;
  const budget = trace.budget;
  const timing = trace.timing;
  const parts: string[] = [];
  const modeLabel = researchExecutionModeLabel(run);
  if (modeLabel) parts.push(modeLabel);
  if (isTraceRecord(routing)) {
    const reason = routing.reason;
    if (typeof reason === "string") parts.push(reason);
  }
  if (isTraceRecord(budget)) {
    const modelCalls = budget.model_calls;
    const modelLimit = budget.model_call_limit;
    const toolCalls = budget.tool_calls;
    const toolLimit = budget.tool_call_limit;
    if (
      typeof modelCalls === "number" &&
      typeof modelLimit === "number" &&
      typeof toolCalls === "number" &&
      typeof toolLimit === "number"
    ) {
      parts.push(`模型 ${modelCalls}/${modelLimit} 次，检索 ${toolCalls}/${toolLimit} 次`);
    }
  }
  if (isTraceRecord(timing)) {
    const duration = timing.total_duration_ms;
    if (typeof duration === "number") parts.push(`耗时 ${(duration / 1_000).toFixed(1)} 秒`);
  }
  return parts.length ? parts.join("；") : null;
}
