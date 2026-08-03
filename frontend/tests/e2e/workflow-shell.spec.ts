import { expect, test, type Page, type Route } from "@playwright/test";

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
const run = {
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
const plan = {
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

async function fulfillWorkflowRequest(route: Route): Promise<void> {
  const path = new URL(route.request().url()).pathname;
  if (path.endsWith("/auth/me")) return route.fulfill({ json: user });
  if (path.endsWith("/collections")) {
    return route.fulfill({ json: { items: [workspace], next_cursor: null } });
  }
  if (path.endsWith(`/collections/${workspaceId}`)) return route.fulfill({ json: workspace });
  if (path.endsWith(`/collections/${workspaceId}/plan`)) return route.fulfill({ json: plan });
  if (path.endsWith("/search-runs/current")) return route.fulfill({ json: run });
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
        status: "completed",
        candidate_counts: run.candidate_counts,
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
  await page.route("http://127.0.0.1:8000/api/v1/**", fulfillWorkflowRequest);
  await page.goto(`/workspace/${workspaceId}/run?run=${runId}`);
}

test("检索完成后在连续画布中进入候选筛选与集合确认", async ({ page }) => {
  await openCompletedRun(page);

  await expect(
    page.getByRole("heading", { name: "检索已经完成，可以查看候选结果。" }),
  ).toBeVisible();
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
