import { expect, test, type Page, type Route } from "@playwright/test";

import type { ResearchPlan, SearchProgressEvent, SearchRun } from "@/api/types";

const workspaceId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";

const user = {
  id: "33333333-3333-4333-8333-333333333333",
  email: "ui-check@example.test",
  display_name: "界面验收",
  created_at: "2026-08-01T00:00:00Z",
};
const workspace = {
  id: workspaceId,
  name: "城市绿地与老年心理健康",
  description: null,
  research_question: "城市绿地可达性如何影响老年人的心理健康？",
  status: "active",
  workflow_stage: "retrieving",
  workflow_stage_display: { label: "文献筛选", description: "候选文献正在准备" },
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};
const run: SearchRun = {
  id: runId,
  collection_id: workspaceId,
  research_plan_id: "44444444-4444-4444-8444-444444444444",
  status: "completed",
  stage: "completed",
  attempt_no: 1,
  provider_summary: { openalex: { status: "completed", candidate_count: 12 } },
  candidate_counts: {
    raw_candidate_count: 12,
    deduplicated_candidate_count: 8,
    included_candidate_count: 6,
    citation_enriched_count: 6,
  },
  error_code: null,
  error_message: null,
  started_at: "2026-08-01T00:00:00Z",
  finished_at: "2026-08-01T00:01:00Z",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:01:00Z",
};
const runningRun: SearchRun = {
  ...run,
  status: "running",
  stage: "relevance_assessment",
  provider_summary: {
    openalex: { status: "completed", candidate_count: 28 },
    crossref: { status: "completed", candidate_count: 22 },
    arxiv: { status: "failed", error: "arXiv 本次暂未返回。" },
  },
  candidate_counts: {
    raw_candidate_count: 56,
    deduplicated_candidate_count: 52,
    included_candidate_count: 50,
    relevance_total_count: 50,
    relevance_completed_count: 18,
    relevance_failed_count: 0,
  },
  finished_at: null,
  updated_at: "2026-08-01T00:00:18Z",
};
const relevanceProgressEvent: SearchProgressEvent = {
  run_id: runId,
  status: "running",
  stage: "relevance_assessment",
  provider_summary: runningRun.provider_summary,
  candidate_counts: runningRun.candidate_counts,
  message: "已完成 18 条候选的统一相关性分析。",
};
const plan: ResearchPlan = {
  id: run.research_plan_id,
  collection_id: workspaceId,
  revision: 1,
  raw_request: workspace.research_question,
  status: "confirmed",
  direction_options: [
    {
      id: "green-space",
      title: "城市绿地与老年心理健康",
      summary: "关注绿地暴露、可达性与老年心理健康之间的关系。",
      subtopics: ["green space exposure", "mental well-being", "older adults"],
    },
  ],
  selected_direction_id: "green-space",
  scope: {
    suggested: { start_year: 2020, end_year: 2026, languages: ["zh", "en"] },
    confirmed: { start_year: 2020, end_year: 2026, languages: ["zh", "en"] },
  },
  query_plan: {
    selected_direction_id: "green-space",
    queries: [{ provider: "openalex", query: "green space accessibility mental health" }],
  },
  model_snapshot: {},
  error_code: null,
  error_message: null,
  confirmed_at: "2026-08-01T00:00:00Z",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};
const candidate = {
  candidate_id: "candidate-1",
  doi: "10.1000/example.1",
  title: "Urban green space exposure and mental well-being in later life",
  language: "en",
  authors: [{ name: "Lin Wei" }, { name: "Zhang Min" }],
  abstract:
    "This study examines green space exposure and mental well-being among older adults in urban neighbourhoods.",
  published_year: 2024,
  venue: "Journal of Environmental Health",
  document_type: "article",
  citation_counts_by_source: { openalex: 7 },
  links: { landing_url: null, open_access_url: null, fulltext_url: null },
  is_open_access: true,
  triage: { included: true, exclusion_reasons: [], warnings: [] },
  relevance_state: "completed",
  relevance_assessment: {
    level: "core",
    study_focus: "研究城市绿地暴露与老年人心理健康之间的关系。",
    reason: "研究对象、绿地暴露和心理健康结果与当前方向直接对应。",
    helpful_aspect: "可用于审核绿地可达性与老年心理健康之间的关联证据。",
    limitations: ["当前判断只依据标题和摘要。"],
    recommendation: "建议优先核验题录并获取全文。",
    evidence: [
      {
        source_field: "abstract",
        quote: "green space exposure and mental well-being among older adults",
      },
    ],
  },
  relevance_error: null,
  citation: { status: "ready", doi: "10.1000/example.1", url: "https://doi.org/10.1000/example.1" },
};
const chineseCandidate = {
  ...candidate,
  candidate_id: "candidate-2",
  doi: "10.1000/example.2",
  title: "城市绿地可达性与老年人心理健康",
  language: "zh",
  relevance_assessment: {
    ...candidate.relevance_assessment,
    level: "background",
    study_focus: "研究城市绿地可达性与老年人心理健康之间的关系。",
    reason: "可补充当前研究的本地语境和背景信息。",
    evidence: [{ source_field: "title", quote: "城市绿地可达性与老年人心理健康" }],
  },
};

async function fulfillWorkflowRequest(
  route: Route,
  activeRun: SearchRun = run,
  activeWorkspace = workspace,
  activePlan = plan,
): Promise<void> {
  const path = new URL(route.request().url()).pathname;
  if (path.endsWith("/auth/me")) return route.fulfill({ json: user });
  if (path.endsWith("/collections")) {
    return route.fulfill({ json: { items: [activeWorkspace], next_cursor: null } });
  }
  if (path.endsWith(`/collections/${workspaceId}`)) return route.fulfill({ json: activeWorkspace });
  if (path.endsWith(`/collections/${workspaceId}/plan`)) return route.fulfill({ json: activePlan });
  if (path.endsWith("/search-runs/current")) return route.fulfill({ json: activeRun });
  if (path.endsWith(`/search-runs/${runId}/candidates`)) {
    const filter = new URL(route.request().url()).searchParams.get("filter") ?? "all";
    const visibleItems =
      filter === "priority"
        ? [candidate]
        : filter === "zh"
          ? [chineseCandidate]
          : filter === "en"
            ? [candidate]
            : [candidate, chineseCandidate];
    return route.fulfill({
      json: {
        run_id: runId,
        status: activeRun.status,
        candidate_counts: activeRun.candidate_counts,
        items: visibleItems.map((currentCandidate) => ({
          candidate: currentCandidate,
          is_selected: false,
          fulltext: null,
        })),
        page: { limit: 20, total: visibleItems.length, next_cursor: null },
        selection: {
          selected_count: 0,
          needs_fulltext_count: 0,
          fulltext_in_progress_count: 0,
          ready_for_admission_count: 0,
          blocked_count: 0,
        },
      },
    });
  }
  if (path.endsWith("/documents")) {
    return route.fulfill({
      json: {
        collection_id: workspaceId,
        documents: [],
        summary: {
          active_document_count: 1,
          researchable_document_count: 0,
          ingestion_status_counts: { pending: 1 },
        },
      },
    });
  }
  return route.fulfill({ status: 404, json: { detail: { message: `未处理的模拟请求：${path}` } } });
}

async function openCompletedRun(page: Page): Promise<void> {
  await page.addInitScript(() =>
    localStorage.setItem("academic-search.access-token", "mock-token"),
  );
  await page.route("http://127.0.0.1:8000/api/v1/**", (route) => fulfillWorkflowRequest(route));
  await page.goto(`/workspace/${workspaceId}/run?run=${runId}`);
}

async function openRunningRun(page: Page): Promise<void> {
  await page.addInitScript(() =>
    localStorage.setItem("academic-search.access-token", "mock-token"),
  );
  await page.route("http://127.0.0.1:8000/api/v1/**", (route) =>
    fulfillWorkflowRequest(route, runningRun),
  );
  await page.route(
    `http://127.0.0.1:8000/api/v1/collections/${workspaceId}/search-runs/${runId}/events`,
    (route) =>
      route.fulfill({
        contentType: "text/event-stream",
        body: `data: ${JSON.stringify(relevanceProgressEvent)}\n\n`,
      }),
  );
  await page.goto(`/workspace/${workspaceId}/run?run=${runId}`);
}

test("检索运行中展示真实的相关性计数和来源失败说明", async ({ page }) => {
  await openRunningRun(page);

  await expect(
    page.getByRole("heading", { name: "已找到 50 篇候选，正在统一判断相关性。" }),
  ).toBeVisible();
  await expect(page.getByText("已分析 18 / 50 篇", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("已完成 18 条候选的统一相关性分析。")).toBeVisible();
  await expect(page.getByText("刚刚收到进度更新")).toBeVisible();
  await expect(
    page.getByText("1 个来源暂未返回，系统仍会继续处理其他来源已返回的候选。"),
  ).toBeVisible();
});

test("检索完成后在连续画布中进入候选筛选与集合确认", async ({ page }) => {
  await openCompletedRun(page);

  await expect(page.getByRole("heading", { name: "6 篇候选文献，已经准备好。" })).toBeVisible();
  await page.getByRole("button", { name: "开始筛选" }).click();

  await expect(page).toHaveURL(new RegExp(`/workspace/${workspaceId}/results\\?run=${runId}`));
  await expect(
    page.getByRole("heading", { name: "把候选记录收敛成可研究的文献集合。" }),
  ).toBeVisible();
  await expect(page.getByLabel("候选文献检查器")).toBeVisible();
  await expect(page.getByText("英文文献").first()).toBeVisible();
  await expect(page.getByText("核心相关").first()).toBeVisible();
  await expect(page.getByLabel("候选文献检查器")).toContainText("为什么保留这篇候选");
  await page.getByRole("button", { name: "优先审核" }).click();
  await expect(page.locator(".candidate-table tbody tr")).toHaveCount(1);
  await page.getByText("查看标题和摘要依据").click();
  await expect(page.getByText("摘要依据", { exact: true })).toBeVisible();
  await expect(page.getByText("说明")).toBeVisible();
  await page.getByRole("button", { name: "中文文献" }).click();
  await expect(page.getByRole("table").getByText("城市绿地可达性与老年人心理健康")).toBeVisible();
  await expect(page.locator(".candidate-table tbody tr")).toHaveCount(1);
  await expect(page.getByLabel("候选文献检查器")).toContainText("城市绿地可达性与老年人心理健康");
  await expect(page.getByRole("button", { name: "待确认集合 1 篇" })).toBeEnabled();

  await page.getByRole("button", { name: "待确认集合 1 篇" }).click();
  await expect(page.getByTestId("collection-confirm-dialog")).toBeVisible();
});

test("窄屏仍可查看候选检查器与集合确认", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openCompletedRun(page);

  await page.getByRole("button", { name: "开始筛选" }).click();
  await expect(page.getByLabel("候选文献检查器")).toBeVisible();
  await page.getByRole("button", { name: "待确认集合 1 篇" }).click();
  await expect(page.getByTestId("collection-confirm-dialog")).toBeVisible();
});

test("候选相关性只支持运行级取消与整批重试", async ({ page }) => {
  let activeRun = runningRun;
  let cancelRequests = 0;
  let retryRequests = 0;
  const citationEnrichingRun = {
    ...runningRun,
    stage: "citation_enrichment" as const,
  };

  await page.addInitScript(() =>
    localStorage.setItem("academic-search.access-token", "mock-token"),
  );
  await page.route("http://127.0.0.1:8000/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith(`/search-runs/${runId}/relevance/cancel`)) {
      expect(route.request().method()).toBe("POST");
      cancelRequests += 1;
      activeRun = {
        ...runningRun,
        status: "cancelled",
        stage: "completed",
        candidate_counts: { ...runningRun.candidate_counts, relevance_failed_count: 32 },
      };
      return route.fulfill({ json: activeRun });
    }
    if (path.endsWith(`/search-runs/${runId}/relevance/retry`)) {
      expect(route.request().method()).toBe("POST");
      retryRequests += 1;
      activeRun = runningRun;
      return route.fulfill({
        status: 202,
        json: {
          run_id: runId,
          status: "running",
          candidate_counts: runningRun.candidate_counts,
          candidates: [candidate, chineseCandidate],
        },
      });
    }
    return fulfillWorkflowRequest(route, activeRun);
  });

  await page.goto(`/workspace/${workspaceId}/results?run=${runId}`);
  await expect(page.getByRole("button", { name: "取消相关性分析" })).toBeVisible();
  await page.getByRole("button", { name: "取消相关性分析" }).click();
  await expect.poll(() => cancelRequests).toBe(1);
  await expect(page.getByRole("button", { name: "重新分析全部候选理由" })).toBeVisible();

  await page.getByRole("button", { name: "重新分析全部候选理由" }).click();
  await expect.poll(() => retryRequests).toBe(1);
  await expect(page.getByRole("button", { name: "取消相关性分析" })).toBeVisible();
  await expect(page.getByText("正在重新分析当前完整候选集合。")).toBeVisible();

  activeRun = citationEnrichingRun;
  await page.reload();
  await expect(page.getByRole("button", { name: "取消相关性分析" })).not.toBeVisible();
  await expect(page.getByRole("button", { name: "重新分析全部候选理由" })).not.toBeVisible();
});

test("详情页为不可重试的全文失败提供授权上传恢复路径", async ({ page }) => {
  const failedFulltext = {
    search_run_id: runId,
    candidate_id: candidate.candidate_id,
    attempt_no: 1,
    status: "failed" as const,
    document: null,
    error: {
      code: "remote_error",
      message: "全文来源返回 HTTP 403。",
      retryable: false,
    },
    requested_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T00:00:03Z",
  };

  await page.addInitScript(() =>
    localStorage.setItem("academic-search.access-token", "mock-token"),
  );
  await page.route("http://127.0.0.1:8000/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith(`/search-runs/${runId}/candidates/${candidate.candidate_id}/citation`)) {
      return route.fulfill({
        json: {
          candidate_id: candidate.candidate_id,
          format: "gb_t_7714_2015_numeric",
          text: "[1] Urban green space exposure and mental well-being in later life.",
        },
      });
    }
    if (path.endsWith(`/search-runs/${runId}/candidates/${candidate.candidate_id}`)) {
      return route.fulfill({
        json: { candidate, is_selected: true, fulltext: failedFulltext },
      });
    }
    return fulfillWorkflowRequest(route);
  });

  await page.goto(`/workspace/${workspaceId}/paper/${candidate.candidate_id}?run=${runId}`);

  await expect(page.getByText("全文暂不可用", { exact: true })).toBeVisible();
  await expect(page.getByText("全文来源返回 HTTP 403。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "选择有权处理的 PDF" })).toBeVisible();
  await expect(page.getByLabel("上传有权处理的 PDF")).toBeVisible();
  await expect(page.locator(".citation-preview pre")).toContainText("Urban green space exposure");
});

test("失败的意图分析会重新生成计划而非重复读取历史失败", async ({ page }) => {
  const failedWorkspace = {
    ...workspace,
    workflow_stage: "failed",
    workflow_stage_display: { label: "任务解析失败", description: "可以重新生成研究计划" },
  };
  const failedPlan: ResearchPlan = {
    ...plan,
    status: "failed",
    direction_options: [],
    selected_direction_id: null,
    scope: {},
    query_plan: {},
    error_code: "intent_model_request_failed",
    error_message: "研究意图分析模型暂时不可用，未生成检索计划。",
    confirmed_at: null,
  };
  const regeneratingPlan: ResearchPlan = {
    ...failedPlan,
    id: "55555555-5555-4555-8555-555555555555",
    revision: 2,
    status: "generating",
    error_code: null,
    error_message: null,
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T00:00:00Z",
  };
  let activePlan: ResearchPlan = failedPlan;
  let regenerateRequestCount = 0;

  await page.addInitScript(() =>
    localStorage.setItem("academic-search.access-token", "mock-token"),
  );
  await page.route("http://127.0.0.1:8000/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith(`/collections/${workspaceId}/plan/regenerate`)) {
      expect(route.request().method()).toBe("POST");
      expect(route.request().postDataJSON()).toEqual({ raw_request: failedPlan.raw_request });
      regenerateRequestCount += 1;
      activePlan = regeneratingPlan;
      return route.fulfill({ status: 202, json: regeneratingPlan });
    }
    return fulfillWorkflowRequest(route, run, failedWorkspace, activePlan);
  });

  await page.goto(`/workspace/${workspaceId}/run`);

  await expect(page.getByText("这次解析没有完成", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重新生成计划" }).click();
  await expect.poll(() => regenerateRequestCount).toBe(1);
  await expect(page.getByRole("heading", { name: "正在理解这项研究。" })).toBeVisible();
});
