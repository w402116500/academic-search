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
 * 后端在运行结束时写入 candidate_count；运行中的新链路会先写入
 * included_candidate_count。旧运行仍兼容此前的 included / total 字段。
 */
export function searchRunCandidateCount(candidateCounts: Record<string, number>): number {
  return (
    candidateCounts.candidate_count ??
    candidateCounts.included_candidate_count ??
    candidateCounts.included ??
    candidateCounts.total ??
    0
  );
}

/**
 * 相关性 Agent 会发布完整候选集合的总数、已完成数和失败数。这里仅转换服务端的真实计数，
 * 不推算百分比或剩余时间，避免等待页向用户展示无法验证的进度。
 */
export function searchRunRelevanceProgress(candidateCounts: Record<string, number>): {
  total: number;
  completed: number;
  failed: number;
} {
  return {
    total: candidateCounts.relevance_total_count ?? 0,
    completed: candidateCounts.relevance_completed_count ?? 0,
    failed: candidateCounts.relevance_failed_count ?? 0,
  };
}

/** 检索运行超过该时长没有任何新事件时，允许用户手动确认进度流是否仍连接。 */
export const SEARCH_RUN_PROGRESS_STALL_MS = 15_000;

/**
 * 首条 SSE 事件尚未到达时，也要从连接建立时开始计算静默时长；否则真正卡住的
 * 首次连接永远不会展示恢复入口。终态判断仍由页面根据 search-run 状态负责。
 */
export function isSearchRunProgressStalled(
  lastEventAt: number | null,
  streamStartedAt: number | null,
  now: number,
): boolean {
  const latestSignalAt = lastEventAt ?? streamStartedAt;
  return latestSignalAt !== null && now - latestSignalAt > SEARCH_RUN_PROGRESS_STALL_MS;
}

/** 全文轮询只应在异步获取阶段继续；拒绝和等待用户授权上传均为终态。 */
export function isFulltextTerminal(status: FulltextStatus | undefined): boolean {
  return ["available", "failed", "rejected", "requires_upload"].includes(status ?? "");
}

/**
 * 有 DOI 的候选可以发起全文核验。Worker 会先按需补齐正式题录，随后才下载全文；
 * 因此浏览器不能把“当前尚无 ready 题录”误判为无法开始核验。
 */
export function canRequestFulltext(
  candidate: Candidate,
  fulltext: FulltextResponse | null | undefined,
): boolean {
  return Boolean(candidate.doi && !fulltext);
}

/** 为题录非 ready 的不同原因提供准确的正式引用提示。 */
export function citationReadinessMessage(citation: CitationMetadata | null): string {
  switch (citation?.status) {
    case "conflict":
      return "题录元数据存在冲突，暂不能生成正式引用。全文核验会再次尝试补齐题录。";
    case "partial":
      return "题录信息不完整，暂不能生成正式引用。全文核验会再次尝试补齐题录。";
    case "unresolved":
      return "题录核验尚未完成，暂不能生成正式引用。";
    case "ready":
      return "题录已通过 DOI 核验，可以生成正式引用。";
    default:
      return "题录尚未核验完成，暂不能生成正式引用。";
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
  if (fulltext.status === "requires_upload") return "需要上传已授权 PDF";
  return "全文处理中";
}

export type FulltextVerificationPresentation = {
  tone: "waiting" | "processing" | "ready" | "blocked";
  label: string;
  detail: string;
  retryable: boolean;
};

/**
 * 核验任务页直接呈现 Worker 公开的真实状态。题录补齐属于 Worker 内的严格前置条件，
 * 但它没有独立的公开阶段，因此不能在前端伪造一条不存在的“题录处理中”状态。
 */
export function presentFulltextVerification(
  fulltext: FulltextResponse | null | undefined,
): FulltextVerificationPresentation {
  if (!fulltext) {
    return {
      tone: "waiting",
      label: "尚未开始核验",
      detail: "该候选仍在本次准备清单中，尚未投递题录与全文核验任务。",
      retryable: false,
    };
  }
  if (fulltext.status === "queued") {
    return {
      tone: "waiting",
      label: "等待核验任务",
      detail: "题录与全文核验已安排，正在等待 Worker 接手。",
      retryable: false,
    };
  }
  if (fulltext.status === "downloading") {
    return {
      tone: "processing",
      label: "正在获取全文",
      detail: "正在获取可公开处理的论文全文。",
      retryable: false,
    };
  }
  if (fulltext.status === "validating") {
    return {
      tone: "processing",
      label: "正在校验 PDF",
      detail: "全文已获取，正在校验文件是否可作为研究材料。",
      retryable: false,
    };
  }
  if (fulltext.status === "requires_upload") {
    return {
      tone: "blocked",
      label: "需要上传已授权 PDF",
      detail:
        fulltext.error?.message ||
        "未找到可处理的开放获取 PDF。确认有权处理后，可从文献详情上传文件。",
      retryable: false,
    };
  }
  if (fulltext.status === "available") {
    return {
      tone: "ready",
      label: "已通过核验",
      detail: "题录与可处理全文均已就绪，可以加入待确认集合。",
      retryable: false,
    };
  }
  return {
    tone: "blocked",
    label: fulltext.status === "rejected" ? "未通过全文准入" : "全文暂不可用",
    detail: fulltext.error?.message || "当前文献暂时不能作为研究集合的正文来源。",
    retryable: fulltext.error?.retryable ?? false,
  };
}
