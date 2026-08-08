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
    relevance_analyzed_count: 18,
    relevance_excluded_count: 0,
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
  message: "正在分析候选相关性。",
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
    recommendation: "建议加入研究集合后跟进入库状态。",
    evidence: [
      {
        source_field: "abstract",
        quote: "green space exposure and mental well-being among older adults",
      },
    ],
  },
  relevance_error: null,
  citation: { status: "ready", doi: "10.1000/example.1", url: "https://doi.org/10.1000/example.1" },
  pdf_availability: { status: "available" },
};
const chineseCandidate = {
  ...candidate,
  candidate_id: "candidate-2",
  doi: "10.1000/example.2",
  title: "城市绿地可达性与老年人心理健康",
  language: "zh",
  pdf_availability: { status: "requires_upload" },
  relevance_assessment: {
    ...candidate.relevance_assessment,
    level: "background",
    study_focus: "研究城市绿地可达性与老年人心理健康之间的关系。",
    reason: "可补充当前研究的本地语境和背景信息。",
    evidence: [{ source_field: "title", quote: "城市绿地可达性与老年人心理健康" }],
  },
};

interface WorkflowMockState {
  selectedIds: Set<string>;
  bibliographyEntries: Array<Record<string, unknown>>;
}

interface WorkflowMockCandidate {
  candidate_id: string;
  title: string;
  authors: Array<{ name: string }>;
  doi: string | null;
  venue: string | null;
  published_year: number | null;
  citation: { status: string };
  pdf_availability?: { status: string } | null;
}

function createWorkflowMockState(): WorkflowMockState {
  return { selectedIds: new Set<string>(), bibliographyEntries: [] };
}

function findWorkflowCandidate(candidateId: string): WorkflowMockCandidate | undefined {
  return [candidate, chineseCandidate].find((item) => item.candidate_id === candidateId);
}

function upsertBibliographyEntry(state: WorkflowMockState, candidateId: string): void {
  if (state.bibliographyEntries.some((entry) => entry.source_candidate_id === candidateId)) return;
  const currentCandidate = findWorkflowCandidate(candidateId);
  if (!currentCandidate) return;
  const pdfStatus = currentCandidate.pdf_availability?.status ?? "requires_upload";
  state.bibliographyEntries.push({
    id: `entry-${candidateId}`,
    collection_id: workspaceId,
    source_search_run_id: runId,
    source_candidate_id: candidateId,
    title: currentCandidate.title,
    authors: currentCandidate.authors,
    doi: currentCandidate.doi,
    venue: currentCandidate.venue,
    publication_year: currentCandidate.published_year,
    citation_status: currentCandidate.citation.status,
    citation_text: null,
    pdf_status: pdfStatus,
    content_status: pdfStatus === "available" ? "pending_auto_download" : "requires_upload",
    paper_id: null,
    document_id: null,
    created_at: "2026-08-01T00:02:00Z",
    updated_at: "2026-08-01T00:02:00Z",
  });
}

async function fulfillWorkflowRequest(
  route: Route,
  activeRun: SearchRun = run,
  activeWorkspace = workspace,
  activePlan = plan,
  state: WorkflowMockState = createWorkflowMockState(),
): Promise<void> {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;
  const method = request.method();
  if (path.endsWith("/auth/me")) return route.fulfill({ json: user });
  if (path.endsWith("/collections")) {
    return route.fulfill({ json: { items: [activeWorkspace], next_cursor: null } });
  }
  if (path.endsWith(`/collections/${workspaceId}`)) return route.fulfill({ json: activeWorkspace });
  if (path.endsWith(`/collections/${workspaceId}/plan`)) return route.fulfill({ json: activePlan });
  if (path.endsWith("/search-runs/current")) return route.fulfill({ json: activeRun });
  const selectionPath = `/search-runs/${runId}/candidate-selection`;
  if (path.endsWith(selectionPath) && method === "PATCH") {
    const payload = request.postDataJSON() as { candidate_ids: string[]; selected: boolean };
    for (const candidateId of payload.candidate_ids) {
      if (payload.selected) state.selectedIds.add(candidateId);
      else state.selectedIds.delete(candidateId);
    }
    return route.fulfill({ json: { run_id: runId, selected_count: state.selectedIds.size } });
  }
  if (path.endsWith(selectionPath) && method === "DELETE") {
    state.selectedIds.clear();
    return route.fulfill({ json: { run_id: runId, selected_count: 0 } });
  }
  if (path.endsWith(`${selectionPath}/admission`) && method === "POST") {
    const admittedCandidateIds = [...state.selectedIds];
    for (const candidateId of admittedCandidateIds) upsertBibliographyEntry(state, candidateId);
    state.selectedIds.clear();
    return route.fulfill({
      json: {
        run_id: runId,
        selected_count: 0,
        admitted_count: admittedCandidateIds.length,
        already_joined_count: 0,
        blocked_count: 0,
        items: [],
      },
    });
  }
  if (path.endsWith(`/search-runs/${runId}/candidates`)) {
    const filter = url.searchParams.get("filter") ?? "all";
    const visibleItems =
      filter === "priority"
        ? [candidate]
        : filter === "zh"
          ? [chineseCandidate]
          : filter === "en"
            ? [candidate]
            : filter === "selected"
              ? [candidate, chineseCandidate].filter((item) =>
                  state.selectedIds.has(item.candidate_id),
                )
              : [candidate, chineseCandidate];
    return route.fulfill({
      json: {
        run_id: runId,
        status: activeRun.status,
        candidate_counts: activeRun.candidate_counts,
        items: visibleItems.map((currentCandidate) => ({
          candidate: currentCandidate,
          is_selected: state.selectedIds.has(currentCandidate.candidate_id),
          fulltext: null,
        })),
        page: { limit: 20, total: visibleItems.length, next_cursor: null },
        selection: {
          selected_count: state.selectedIds.size,
          needs_fulltext_count: 0,
          fulltext_in_progress_count: 0,
          ready_for_admission_count: state.selectedIds.size,
          blocked_count: 0,
        },
      },
    });
  }
  if (path.endsWith("/documents")) {
    const pendingCount = state.bibliographyEntries.filter(
      (entry) => entry.content_status === "pending_auto_download",
    ).length;
    const readyCount = state.bibliographyEntries.filter(
      (entry) => entry.content_status === "researchable",
    ).length;
    return route.fulfill({
      json: {
        collection_id: workspaceId,
        bibliography_entries: state.bibliographyEntries,
        documents: [],
        summary: {
          bibliography_entry_count: state.bibliographyEntries.length,
          active_document_count: pendingCount + readyCount,
          researchable_document_count: readyCount,
          ingestion_status_counts: pendingCount ? { pending: pendingCount } : {},
        },
      },
    });
  }
  return route.fulfill({ status: 404, json: { detail: { message: `未处理的模拟请求：${path}` } } });
}

async function openCompletedRun(
  page: Page,
  state: WorkflowMockState = createWorkflowMockState(),
): Promise<void> {
  await page.addInitScript(() =>
    localStorage.setItem("academic-search.access-token", "mock-token"),
  );
  await page.route("http://127.0.0.1:8000/api/v1/**", (route) =>
    fulfillWorkflowRequest(route, run, workspace, plan, state),
  );
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
  await expect(
    page.getByText("正在分析候选相关性 · 已分析 18 / 50 篇 · 已排除 0 篇", { exact: true }).first(),
  ).toBeVisible();
  await expect(page.getByText("正在分析候选相关性。")).toBeVisible();
  await expect(page.getByText("刚刚收到进度更新")).toBeVisible();
  await expect(
    page.getByText("1 个来源暂未返回，系统仍会继续处理其他来源已返回的候选。"),
  ).toBeVisible();
});

test("检索完成后在连续画布中进入候选筛选并加入研究集合", async ({ page }) => {
  const state = createWorkflowMockState();
  await openCompletedRun(page, state);

  await expect(page.getByRole("heading", { name: "6 篇候选文献，已经准备好。" })).toBeVisible();
  await page.getByRole("button", { name: "开始筛选" }).click();

  await expect(page).toHaveURL(new RegExp(`/workspace/${workspaceId}/results\\?run=${runId}`));
  await expect(
    page.getByRole("heading", { name: "把候选记录收敛成可研究的文献集合。" }),
  ).toBeVisible();
  await expect(page.getByLabel("文献详情")).toBeVisible();
  await expect(page.getByText("英文文献").first()).toBeVisible();
  await expect(page.getByText("核心相关").first()).toBeVisible();
  await expect(page.getByLabel("文献详情")).toContainText("相关性依据");
  await page.getByRole("button", { name: "优先审核" }).click();
  await expect(page.locator(".candidate-table tbody tr")).toHaveCount(1);
  await page.getByText("查看标题和摘要依据").click();
  await expect(page.getByText("摘要依据", { exact: true })).toBeVisible();
  await expect(page.getByText("说明")).toBeVisible();
  await page.getByRole("button", { name: "中文文献" }).click();
  await expect(page.getByRole("table").getByText("城市绿地可达性与老年人心理健康")).toBeVisible();
  await expect(page.locator(".candidate-table tbody tr")).toHaveCount(1);
  await expect(page.getByLabel("文献详情")).toContainText("城市绿地可达性与老年人心理健康");
  await page.getByLabel("选择 城市绿地可达性与老年人心理健康").check();
  await expect(page.getByRole("button", { name: "加入研究集合（1）" })).toBeEnabled();

  await page.getByRole("button", { name: "加入研究集合（1）" }).click();
  await expect(page).toHaveURL(new RegExp(`/workspace/${workspaceId}/collection`));
  await expect(page.locator(".collection-summary > div").nth(0)).toContainText("1");
  await expect(page.locator(".collection-summary > div").nth(1)).toContainText("0");
  await expect(page.locator(".collection-summary > div").nth(2)).toContainText("1");
  await expect(page.getByText("城市绿地可达性与老年人心理健康")).toBeVisible();
  await expect(page.getByText("需上传 PDF").first()).toBeVisible();
});

test("窄屏仍可查看文献详情与研究集合入口", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openCompletedRun(page);

  await page.getByRole("button", { name: "开始筛选" }).click();
  await expect(page.getByLabel("文献详情")).toBeVisible();
  await page
    .getByLabel("选择 Urban green space exposure and mental well-being in later life")
    .check();
  await expect(page.getByRole("button", { name: "加入研究集合（1）" })).toBeVisible();
});

test("候选相关性分析中不暴露取消或重试控制", async ({ page }) => {
  await openRunningRun(page);
  await page.goto(`/workspace/${workspaceId}/results?run=${runId}`);

  await expect(page.getByRole("button", { name: "取消相关性分析" })).not.toBeVisible();
  await expect(page.getByRole("button", { name: "重新分析全部候选理由" })).not.toBeVisible();
});

test("详情页不暴露旧全文失败原因，并可将需上传 PDF 的候选加入集合", async ({ page }) => {
  const state = createWorkflowMockState();
  const failedCandidate = {
    ...candidate,
    pdf_availability: { status: "requires_upload" },
  };
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
    const request = route.request();
    const path = new URL(route.request().url()).pathname;
    const method = request.method();
    const selectionPath = `/search-runs/${runId}/candidate-selection`;
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
        json: { candidate: failedCandidate, is_selected: false, fulltext: failedFulltext },
      });
    }
    if (path.endsWith(selectionPath) && method === "PATCH") {
      state.selectedIds.add(candidate.candidate_id);
      return route.fulfill({ json: { run_id: runId, selected_count: 1 } });
    }
    if (path.endsWith(`${selectionPath}/admission`) && method === "POST") {
      state.bibliographyEntries.push({
        id: `entry-${candidate.candidate_id}`,
        collection_id: workspaceId,
        source_search_run_id: runId,
        source_candidate_id: candidate.candidate_id,
        title: failedCandidate.title,
        authors: failedCandidate.authors,
        doi: failedCandidate.doi,
        venue: failedCandidate.venue,
        publication_year: failedCandidate.published_year,
        citation_status: "ready",
        citation_text: null,
        pdf_status: "requires_upload",
        content_status: "requires_upload",
        paper_id: null,
        document_id: null,
        created_at: "2026-08-04T00:02:00Z",
        updated_at: "2026-08-04T00:02:00Z",
      });
      state.selectedIds.clear();
      return route.fulfill({
        json: {
          run_id: runId,
          selected_count: 0,
          admitted_count: 1,
          already_joined_count: 0,
          blocked_count: 0,
          items: [],
        },
      });
    }
    return fulfillWorkflowRequest(route, run, workspace, plan, state);
  });

  await page.goto(`/workspace/${workspaceId}/paper/${candidate.candidate_id}?run=${runId}`);

  await expect(page.getByText("需上传 PDF", { exact: true })).toBeVisible();
  await expect(page.getByText("全文来源返回 HTTP 403。", { exact: true })).not.toBeVisible();
  await expect(page.getByRole("button", { name: "选择有权处理的 PDF" })).not.toBeVisible();
  await expect(page.getByLabel("上传有权处理的 PDF")).not.toBeVisible();
  await expect(page.locator(".citation-preview pre")).toContainText("Urban green space exposure");
  await page.getByRole("button", { name: "加入研究集合" }).click();
  await expect(page).toHaveURL(new RegExp(`/workspace/${workspaceId}/collection`));
  await expect(page.locator(".collection-summary > div").nth(0)).toContainText("1");
  await expect(page.locator(".collection-summary > div").nth(2)).toContainText("1");
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
