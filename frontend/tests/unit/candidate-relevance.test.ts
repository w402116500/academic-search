import { describe, expect, it } from "vitest";

import type { Candidate } from "@/api/types";
import { presentCandidateRelevance } from "@/features/search/candidate-relevance";

const candidate: Candidate = {
  candidate_id: "candidate-1",
  doi: "10.1000/example.1",
  title: "Green space exposure and mental well-being among older adults",
  language: "en",
  authors: [{ name: "Lin Wei" }],
  abstract:
    "This study examines green space exposure and mental well-being among older adults in urban neighbourhoods.",
  published_year: 2024,
  venue: "Journal of Environmental Health",
  document_type: "article",
  citation_counts_by_source: {},
  links: { landing_url: null, open_access_url: null, fulltext_url: null },
  is_open_access: true,
  triage: { included: true, exclusion_reasons: [], warnings: [] },
  relevance_state: "completed",
  relevance_assessment: {
    level: "core",
    study_focus: "考察城市绿地暴露与老年人心理健康之间的关系。",
    reason: "研究对象和核心关系与当前方向直接对应。",
    helpful_aspect: "可用于梳理绿地暴露和心理健康的关联证据。",
    limitations: ["仅能依据公开摘要判断。"],
    recommendation: "建议优先查看全文。",
    evidence: [{ source_field: "title", quote: "Green space exposure" }],
  },
  relevance_error: null,
  citation: {
    status: "ready",
    title: "Green space exposure and mental well-being among older adults",
    authors: [],
    missing_fields: [],
    doi: "10.1000/example.1",
    url: null,
  },
};

describe("candidate relevance presentation", () => {
  it("只展示服务端 Agent 已核验的候选理由和证据", () => {
    const presentation = presentCandidateRelevance(candidate);

    expect(presentation.tierLabel).toBe("核心相关");
    expect(presentation.studyFocus).toContain("城市绿地暴露");
    expect(presentation.relevanceSummary).toContain("直接对应");
    expect(presentation.evidence).toEqual([{ label: "标题依据", quote: "Green space exposure" }]);
  });

  it("模型失败时显示明确状态并保留可重试动作，不生成关键词替代理由", () => {
    const presentation = presentCandidateRelevance({
      ...candidate,
      relevance_state: "failed",
      relevance_assessment: null,
      relevance_error: {
        code: "candidate_relevance_model_unavailable",
        message: "候选相关性模型暂时不可用，请稍后重试。",
        retryable: true,
      },
    });

    expect(presentation.tierLabel).toBe("分析未完成");
    expect(presentation.relevanceSummary).toContain("暂时不可用");
    expect(presentation.canRetry).toBe(true);
    expect(presentation.evidence).toHaveLength(0);
  });

  it("未通过基础筛选的记录明确说明没有进入 Agent 分析", () => {
    const presentation = presentCandidateRelevance({
      ...candidate,
      relevance_state: "skipped",
      relevance_assessment: null,
      relevance_error: null,
    });

    expect(presentation.tierLabel).toBe("未进入分析");
    expect(presentation.evidenceBoundary).toContain("基础筛选");
    expect(presentation.canRetry).toBe(false);
  });
});
