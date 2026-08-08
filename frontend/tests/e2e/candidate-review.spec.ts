import { expect, test, type Page, type Route } from "@playwright/test";

const workspaceId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";

const user = {
  id: "33333333-3333-4333-8333-333333333333",
  email: "review-ui@example.test",
  display_name: "候选审核验收",
  created_at: "2026-08-03T00:00:00Z",
};

const workspace = {
  id: workspaceId,
  name: "社区步行性与成年人的身体活动",
  description: null,
  research_question: "社区步行性如何影响成年人的日常身体活动？",
  status: "active",
  workflow_stage: "screening",
  workflow_stage_display: { label: "文献筛选", description: "审核候选文献" },
  created_at: "2026-08-03T00:00:00Z",
  updated_at: "2026-08-03T00:00:00Z",
};

const run = {
  id: runId,
  collection_id: workspaceId,
  research_plan_id: "44444444-4444-4444-8444-444444444444",
  status: "completed",
  stage: "completed",
  attempt_no: 1,
  provider_summary: { openalex: { status: "completed", candidate_count: 3 } },
  candidate_counts: {
    raw_candidate_count: 3,
    deduplicated_candidate_count: 3,
    included_candidate_count: 3,
    citation_enriched_count: 3,
  },
  error_code: null,
  error_message: null,
  started_at: "2026-08-03T00:00:00Z",
  finished_at: "2026-08-03T00:01:00Z",
  created_at: "2026-08-03T00:00:00Z",
  updated_at: "2026-08-03T00:01:00Z",
};

function candidate(
  candidateId: string,
  title: string,
  language: "zh" | "en",
  pdfStatus: "available" | "requires_upload" = "available",
) {
  return {
    candidate_id: candidateId,
    doi: `10.1000/${candidateId}`,
    title,
    language,
    authors: [{ name: language === "zh" ? "王晨" : "Jordan Lee" }],
    abstract: `${title} 的摘要。`,
    published_year: 2024,
    venue: "Journal of Active Living",
    document_type: "article",
    citation_counts_by_source: { openalex: 4 },
    links: { landing_url: null, open_access_url: null, fulltext_url: null },
    is_open_access: true,
    triage: { included: true, exclusion_reasons: [], warnings: [] },
    relevance_state: "completed",
    relevance_assessment: {
      level: "core",
      study_focus: "研究建成环境与成人身体活动之间的关联。",
      reason: "研究对象、社区步行性和身体活动指标与当前问题直接对应。",
      helpful_aspect: "可用于核对步行性影响身体活动的实证证据。",
      limitations: ["当前判断只依据标题和摘要。"],
      recommendation: "建议优先完成题录与全文核验。",
      evidence: [{ source_field: "title", quote: title }],
    },
    relevance_error: null,
    citation: { status: "ready", doi: `10.1000/${candidateId}`, url: null },
    pdf_availability: { status: pdfStatus },
  };
}

const allCandidates = [
  candidate("candidate-1", "Neighborhood walkability and physical activity among adults", "en"),
  candidate("candidate-2", "社区步行性与成年人身体活动的关联", "zh"),
  candidate(
    "candidate-3",
    "Built environment correlates of daily walking in adults",
    "en",
    "requires_upload",
  ),
];

async function openReviewPage(page: Page): Promise<void> {
  const selectedIds = new Set<string>();
  const bibliographyEntries: Array<Record<string, unknown>> = [];

  function upsertBibliographyEntry(candidateId: string): void {
    if (bibliographyEntries.some((entry) => entry.source_candidate_id === candidateId)) return;
    const currentCandidate = allCandidates.find((item) => item.candidate_id === candidateId);
    if (!currentCandidate) return;
    const pdfStatus = currentCandidate.pdf_availability.status;
    bibliographyEntries.push({
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
      created_at: "2026-08-03T00:02:00Z",
      updated_at: "2026-08-03T00:02:00Z",
    });
  }

  await page.addInitScript(() =>
    localStorage.setItem("academic-search.access-token", "candidate-review-token"),
  );
  await page.route("http://127.0.0.1:8000/api/v1/**", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const { pathname } = url;
    const method = request.method();

    if (pathname.endsWith("/auth/me")) return route.fulfill({ json: user });
    if (pathname.endsWith("/collections")) {
      return route.fulfill({ json: { items: [workspace], next_cursor: null } });
    }
    if (pathname.endsWith(`/collections/${workspaceId}`)) return route.fulfill({ json: workspace });
    if (pathname.endsWith("/search-runs/current")) return route.fulfill({ json: run });
    if (pathname.endsWith("/documents")) {
      const pendingCount = bibliographyEntries.filter(
        (entry) => entry.content_status === "pending_auto_download",
      ).length;
      return route.fulfill({
        json: {
          collection_id: workspaceId,
          bibliography_entries: bibliographyEntries,
          documents: [],
          summary: {
            bibliography_entry_count: bibliographyEntries.length,
            active_document_count: pendingCount,
            researchable_document_count: 0,
            ingestion_status_counts: pendingCount ? { pending: pendingCount } : {},
          },
        },
      });
    }

    const candidatePath = `/search-runs/${runId}/candidates`;
    const selectionPath = `/search-runs/${runId}/candidate-selection`;
    if (pathname.endsWith(selectionPath) && method === "PATCH") {
      const payload = request.postDataJSON() as { candidate_ids: string[]; selected: boolean };
      for (const candidateId of payload.candidate_ids) {
        if (payload.selected) selectedIds.add(candidateId);
        else selectedIds.delete(candidateId);
      }
      return route.fulfill({ json: { run_id: runId, selected_count: selectedIds.size } });
    }
    if (pathname.endsWith(selectionPath) && method === "DELETE") {
      selectedIds.clear();
      return route.fulfill({ json: { run_id: runId, selected_count: 0 } });
    }
    if (pathname.endsWith(`${selectionPath}/admission`) && method === "POST") {
      const admittedCandidateIds = [...selectedIds];
      for (const candidateId of admittedCandidateIds) upsertBibliographyEntry(candidateId);
      selectedIds.clear();
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
    if (pathname.endsWith(candidatePath) && method === "GET") {
      const filter = url.searchParams.get("filter") ?? "all";
      const cursor = url.searchParams.get("cursor");
      let visibleCandidates = allCandidates;
      if (filter === "selected") {
        visibleCandidates = allCandidates.filter(({ candidate_id }) =>
          selectedIds.has(candidate_id),
        );
      }
      if (filter === "zh")
        visibleCandidates = allCandidates.filter(({ language }) => language === "zh");
      if (filter === "en")
        visibleCandidates = allCandidates.filter(({ language }) => language === "en");

      const isFirstAllPage = filter === "all" && !cursor;
      const pageCandidates =
        filter === "all"
          ? isFirstAllPage
            ? visibleCandidates.slice(0, 2)
            : visibleCandidates.slice(2)
          : visibleCandidates;
      return route.fulfill({
        json: {
          run_id: runId,
          status: "completed",
          candidate_counts: run.candidate_counts,
          items: pageCandidates.map((candidate) => ({
            candidate,
            is_selected: selectedIds.has(candidate.candidate_id),
            fulltext: null,
          })),
          page: {
            limit: 20,
            total: visibleCandidates.length,
            next_cursor: isFirstAllPage ? "page-2" : null,
          },
          selection: {
            selected_count: selectedIds.size,
            needs_fulltext_count: 0,
            fulltext_in_progress_count: 0,
            ready_for_admission_count: selectedIds.size,
            blocked_count: 0,
          },
        },
      });
    }
    return route.fulfill({
      status: 404,
      json: { detail: { message: `未处理模拟请求：${pathname}` } },
    });
  });

  await page.goto(`/workspace/${workspaceId}/results?run=${runId}`);
}

test("候选审核在跨页、筛选和刷新后保持选择，并直接加入研究集合", async ({ page }) => {
  await openReviewPage(page);

  const firstTitle = allCandidates[0].title;
  const thirdTitle = allCandidates[2].title;
  const firstCheckbox = page.getByLabel(`选择 ${firstTitle}`);
  await firstCheckbox.check();
  await expect(page.getByLabel("本次准备清单操作")).toContainText("已选 1 篇");

  // 点击行只移动检查器焦点，不能意外改变当前多选状态。
  await page
    .locator(".candidate-table tbody tr")
    .filter({ hasText: firstTitle })
    .locator("td")
    .nth(1)
    .click();
  await expect(firstCheckbox).toBeChecked();

  await page.getByRole("button", { name: "下一页" }).click();
  const thirdCheckbox = page.getByLabel(`选择 ${thirdTitle}`);
  await thirdCheckbox.check();
  await expect(page.getByLabel("本次准备清单操作")).toContainText("已选 2 篇");

  // 刷新会重新从 Redis 准备清单读取选择，而不是依赖浏览器内存。
  await page.reload();
  await expect(page.getByLabel("本次准备清单操作")).toContainText("已选 2 篇");
  await page.getByRole("button", { name: "只看已选" }).click();
  const candidateTable = page.getByRole("table");
  await expect(candidateTable.getByText(firstTitle)).toBeVisible();
  await expect(candidateTable.getByText(thirdTitle)).toBeVisible();

  await page.getByRole("button", { name: "加入研究集合（2）" }).click();
  await expect(page).toHaveURL(new RegExp(`/workspace/${workspaceId}/collection`));
  await expect(page.locator(".collection-summary > div").nth(0)).toContainText("2");
  await expect(page.locator(".collection-summary > div").nth(1)).toContainText("0");
  await expect(page.locator(".collection-summary > div").nth(2)).toContainText("1");
  await expect(page.getByText(firstTitle)).toBeVisible();
  await expect(page.getByText(thirdTitle)).toBeVisible();
  await expect(page.getByText("正在入库", { exact: true })).toBeVisible();
  await expect(page.getByText("需上传 PDF").first()).toBeVisible();
});
