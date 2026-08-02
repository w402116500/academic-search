import type {
  Candidate,
  CitationMetadata,
  FulltextResponse,
  FulltextStatus,
  SearchRunStatus,
  WorkflowStage,
} from "@/api/types";

export type RecoveredSearchRoute =
  "workspace-runner" | "workspace-results" | "workspace-collection";

/**
 * 这些阶段已经越过研究计划确认，刷新或从工作区入口返回时应恢复服务端的当前检索运行。
 */
export function shouldRestoreCurrentSearchRun(stage: WorkflowStage | undefined): boolean {
  return ["retrieving", "screening", "collection_building", "researching"].includes(stage ?? "");
}

/**
 * 当前检索运行仍在处理时保留进度画布；运行结束后交给后端工作区阶段决定下一页。
 */
export function routeForRecoveredSearchRun(
  workflowStage: WorkflowStage,
  searchRunStatus: SearchRunStatus,
): RecoveredSearchRoute {
  if (!["completed", "partial_failed"].includes(searchRunStatus)) return "workspace-runner";
  if (workflowStage === "screening") return "workspace-results";
  if (["collection_building", "researching"].includes(workflowStage)) return "workspace-collection";
  return "workspace-runner";
}

/**
 * 后端在运行结束时写入 candidate_count；旧运行仍兼容此前的 included / total 字段。
 */
export function searchRunCandidateCount(candidateCounts: Record<string, number>): number {
  return candidateCounts.candidate_count ?? candidateCounts.included ?? candidateCounts.total ?? 0;
}

/** 全文轮询只应在异步获取阶段继续，拒绝也是终态而不是“仍在处理中”。 */
export function isFulltextTerminal(status: FulltextStatus | undefined): boolean {
  return ["available", "failed", "rejected"].includes(status ?? "");
}

/**
 * 仅允许题录已通过 DOI 核验的候选发起全文请求，避免前端发送服务端必然拒绝的请求。
 */
export function canRequestFulltext(
  candidate: Candidate,
  fulltext: FulltextResponse | null | undefined,
): boolean {
  return Boolean(candidate.doi && candidate.citation?.status === "ready" && !fulltext);
}

/** 为题录非 ready 的不同原因提供准确的、不会被误解为加载中的状态文案。 */
export function citationReadinessMessage(citation: CitationMetadata | null): string {
  switch (citation?.status) {
    case "conflict":
      return "题录元数据存在冲突，暂不能生成正式引用或获取全文。";
    case "partial":
      return "题录信息不完整，暂不能生成正式引用或获取全文。";
    case "unresolved":
      return "题录核验尚未完成，暂不能生成正式引用或获取全文。";
    case "ready":
      return "题录已通过 DOI 核验，可以生成正式引用。";
    default:
      return "题录尚未核验完成，暂不能生成正式引用或获取全文。";
  }
}

/** 让候选列表直接呈现真实题录状态，避免把不同问题笼统显示为“未核验”。 */
export function citationStatusLabel(citation: CitationMetadata | null | undefined): string {
  switch (citation?.status) {
    case "conflict":
      return "题录存在冲突";
    case "partial":
      return "题录信息不完整";
    case "unresolved":
      return "题录核验失败";
    case "ready":
      return "题录已核验";
    default:
      return "题录待核验";
  }
}

/** 将全文任务状态翻译为结果页与检查器共用的中文标签。 */
export function fulltextStatusLabel(fulltext: FulltextResponse | null | undefined): string {
  if (!fulltext) return "获取全文";
  if (fulltext.status === "available") return "全文已核验";
  if (fulltext.status === "rejected") return "未通过全文准入";
  if (fulltext.status === "failed") return "全文不可用";
  return "全文处理中";
}
