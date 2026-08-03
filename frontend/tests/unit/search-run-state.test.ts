import { describe, expect, it } from "vitest";

import type { Candidate, FulltextResponse } from "@/api/types";
import {
  canRequestFulltext,
  citationReadinessMessage,
  citationStatusLabel,
  isFulltextTerminal,
  routeForRecoveredSearchRun,
  searchRunCandidateCount,
  shouldRestoreCurrentSearchRun,
} from "@/features/research/search-run-state";

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
    citation: { status: citationStatus, doi: "10.1000/example", url: null },
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
    expect(searchRunCandidateCount({ included: 2 })).toBe(2);
  });

  it("将 rejected 作为全文终态", () => {
    expect(isFulltextTerminal("rejected")).toBe(true);
    expect(isFulltextTerminal("validating")).toBe(false);
  });

  it("为带 DOI 且尚未创建任务的候选开放全文核验", () => {
    expect(canRequestFulltext(createCandidate("ready"), null)).toBe(true);
    // 全文 Worker 会先重新补齐题录，题录冲突不能让浏览器提前阻断核验尝试。
    expect(canRequestFulltext(createCandidate("conflict"), null)).toBe(true);
    expect(canRequestFulltext({ ...createCandidate("ready"), doi: null }, null)).toBe(false);
    expect(
      canRequestFulltext(createCandidate("ready"), {
        status: "rejected",
      } as FulltextResponse),
    ).toBe(false);
  });

  it("为题录冲突显示真实原因而不是加载提示", () => {
    expect(citationReadinessMessage(createCandidate("conflict").citation)).toContain("存在冲突");
    expect(citationStatusLabel(createCandidate("conflict").citation)).toBe("题录存在冲突");
    expect(citationStatusLabel(createCandidate("partial").citation)).toBe("题录信息不完整");
    expect(citationStatusLabel(null)).toBe("题录待核验");
  });
});
