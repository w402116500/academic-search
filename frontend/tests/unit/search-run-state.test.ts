import { describe, expect, it } from "vitest";

import type { Candidate, FulltextResponse } from "@/api/types";
import {
  canRequestFulltext,
  candidatePdfAvailabilityLabel,
  citationReadinessMessage,
  citationStatusLabel,
  isSearchRunProgressStalled,
  isFulltextTerminal,
  presentFulltextVerification,
  routeForRecoveredSearchRun,
  searchRunCandidateCount,
  searchRunRelevanceProgress,
  shouldRestoreCurrentSearchRun,
} from "@/features/search/search-run-state";

function createCandidate(citationStatus: NonNullable<Candidate["citation"]>["status"]): Candidate {
  return {
    candidate_id: "candidate-1",
    doi: "10.1000/example",
    title: "Example paper",
    language: "en",
    authors: [],
    abstract: null,
    published_year: 2025,
    venue: null,
    document_type: "journal_article",
    citation_counts_by_source: {},
    links: { landing_url: null, open_access_url: null, fulltext_url: null },
    is_open_access: true,
    triage: null,
    citation: {
      status: citationStatus,
      title: "Example paper",
      authors: [],
      missing_fields: [],
      doi: "10.1000/example",
      url: null,
    },
  };
}

describe("检索运行恢复", () => {
  it("已进入筛选阶段时恢复当前运行并跳转结果页", () => {
    expect(shouldRestoreCurrentSearchRun("screening")).toBe(true);
    expect(routeForRecoveredSearchRun("screening", "completed")).toBe("workspace-results");
  });

  it("仍在检索中的运行保留在进度画布", () => {
    expect(shouldRestoreCurrentSearchRun("retrieving")).toBe(true);
    expect(routeForRecoveredSearchRun("retrieving", "running")).toBe("workspace-runner");
  });

  it("未确认计划时不请求当前检索运行", () => {
    expect(shouldRestoreCurrentSearchRun("plan_review")).toBe(false);
  });
});

describe("检索与全文状态", () => {
  it("优先使用后端最终写入的候选数", () => {
    expect(searchRunCandidateCount({ candidate_count: 49, included: 0, total: 0 })).toBe(49);
    expect(searchRunCandidateCount({ included_candidate_count: 6 })).toBe(6);
    expect(searchRunCandidateCount({ included: 2 })).toBe(2);
  });

  it("只呈现服务端发布的相关性分析计数", () => {
    expect(
      searchRunRelevanceProgress({
        relevance_total_count: 50,
        relevance_analyzed_count: 18,
        relevance_excluded_count: 2,
      }),
    ).toEqual({ total: 50, analyzed: 18, excluded: 2 });
    expect(searchRunRelevanceProgress({})).toEqual({ total: 0, analyzed: 0, excluded: 0 });
  });

  it("首次事件未到达时也会提示检索进度静默", () => {
    expect(isSearchRunProgressStalled(null, 1_000, 15_999)).toBe(false);
    expect(isSearchRunProgressStalled(null, 1_000, 16_001)).toBe(true);
    expect(isSearchRunProgressStalled(8_000, 1_000, 22_000)).toBe(false);
    expect(isSearchRunProgressStalled(8_000, 1_000, 23_001)).toBe(true);
  });

  it("将 rejected 和 requires_upload 作为全文终态", () => {
    expect(isFulltextTerminal("rejected")).toBe(true);
    expect(isFulltextTerminal("requires_upload")).toBe(true);
    expect(isFulltextTerminal("validating")).toBe(false);
  });

  it("保留历史单篇全文任务的 DOI 触发条件", () => {
    expect(canRequestFulltext(createCandidate("ready"), null)).toBe(true);
    // 历史全文 Worker 会先重新补齐题录，题录冲突不能让浏览器提前阻断处理尝试。
    expect(canRequestFulltext(createCandidate("conflict"), null)).toBe(true);
    expect(canRequestFulltext({ ...createCandidate("ready"), doi: null }, null)).toBe(false);
    expect(
      canRequestFulltext(createCandidate("ready"), {
        status: "rejected",
      } as FulltextResponse),
    ).toBe(false);
  });

  it("为候选题录和 PDF 可得性显示稳定用户状态", () => {
    expect(citationReadinessMessage(createCandidate("conflict").citation)).toContain("暂不可用");
    expect(citationStatusLabel(createCandidate("ready").citation)).toBe("题录已核验");
    expect(citationStatusLabel(createCandidate("conflict").citation)).toBe("该题录暂不可用");
    expect(citationStatusLabel(createCandidate("partial").citation)).toBe("该题录暂不可用");
    expect(citationStatusLabel(null)).toBe("该题录暂不可用");
    expect(
      candidatePdfAvailabilityLabel({
        ...createCandidate("ready"),
        pdf_availability: { status: "available" },
      }),
    ).toBe("可自动获取 PDF");
    expect(candidatePdfAvailabilityLabel(createCandidate("ready"))).toBe("需上传 PDF");
  });

  it("将历史全文任务状态转换为稳定 PDF 处理说明", () => {
    expect(presentFulltextVerification(null)).toMatchObject({
      tone: "waiting",
      label: "尚未加入研究集合",
      retryable: false,
    });
    expect(presentFulltextVerification({ status: "queued" } as FulltextResponse)).toMatchObject({
      tone: "waiting",
      label: "等待处理",
    });
    expect(presentFulltextVerification({ status: "validating" } as FulltextResponse)).toMatchObject(
      { tone: "processing", label: "正在校验 PDF" },
    );
    expect(presentFulltextVerification({ status: "available" } as FulltextResponse)).toMatchObject({
      tone: "ready",
      label: "可自动获取 PDF",
    });
    expect(
      presentFulltextVerification({
        status: "requires_upload",
        error: { message: "请上传有权处理的 PDF", retryable: false },
      } as FulltextResponse),
    ).toMatchObject({ tone: "blocked", label: "需要上传已授权 PDF", retryable: false });
    expect(
      presentFulltextVerification({
        status: "failed",
        error: { message: "下载超时", retryable: true },
      } as FulltextResponse),
    ).toMatchObject({ tone: "blocked", label: "需上传 PDF", retryable: true });
  });
});
