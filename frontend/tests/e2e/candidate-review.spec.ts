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

function candidate(candidateId: string, title: string, language: "zh" | "en") {
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
  };
}

const allCandidates = [
  candidate("candidate-1", "Neighborhood walkability and physical activity among adults", "en"),
  candidate("candidate-2", "社区步行性与成年人身体活动的关联", "zh"),
  candidate("candidate-3", "Built environment correlates of daily walking in adults", "en"),
];

function fulltextState(candidateId: string, status: "available" | "validating") {
  return {
    search_run_id: runId,
    candidate_id: candidateId,
    attempt_no: 1,
    status,
    document: {
      staging_object_key: `staging/${candidateId}.pdf`,
      sha256: "a".repeat(64),
      byte_size: 1024,
    },
    error: null,
    requested_at: "2026-08-03T00:00:00Z",
    updated_at: "2026-08-03T00:00:02Z",
  };
}

async function openReviewPage(page: Page): Promise<void> {
  const selectedIds = new Set<string>();
  const verificationStates = new Map<string, "available" | "validating">();
  let pendingDocumentCount = 0;

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
      return route.fulfill({
        json: {
          collection_id: workspaceId,
          documents: [],
          summary: {
            active_document_count: pendingDocumentCount,
            researchable_document_count: 0,
            ingestion_status_counts: { pending: pendingDocumentCount },
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
      verificationStates.clear();
      return route.fulfill({ json: { run_id: runId, selected_count: 0 } });
    }
    if (pathname.endsWith(`${selectionPath}/prepare`) && method === "POST") {
      for (const candidateId of selectedIds) {
        // 模拟同一批次内的真实部分完成：一篇已通过，另一篇仍在校验。
        verificationStates.set(
          candidateId,
          candidateId === "candidate-1" ? "available" : "validating",
        );
      }
      return route.fulfill({
        status: 202,
        json: {
          run_id: runId,
          selected_count: selectedIds.size,
          queued_count: selectedIds.size,
          items: [...selectedIds].map((candidateId) => ({
            candidate_id: candidateId,
            status: "queued",
            message: "题录与全文核验已安排。",
            retryable: false,
          })),
        },
      });
    }
    if (pathname.endsWith(`${selectionPath}/admission`) && method === "POST") {
      const admittedCandidateIds = [...selectedIds].filter(
        (candidateId) => verificationStates.get(candidateId) === "available",
      );
      const admittedCount = admittedCandidateIds.length;
      pendingDocumentCount += admittedCount;
      for (const candidateId of admittedCandidateIds) {
        selectedIds.delete(candidateId);
        verificationStates.delete(candidateId);
      }
      return route.fulfill({
        json: {
          run_id: runId,
          selected_count: admittedCount,
          admitted_count: admittedCount,
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
      const needsFulltextCount = [...selectedIds].filter(
        (candidateId) => !verificationStates.has(candidateId),
      ).length;
      const fulltextInProgressCount = [...selectedIds].filter((candidateId) =>
        ["queued", "downloading", "validating"].includes(verificationStates.get(candidateId) ?? ""),
      ).length;
      const readyForAdmissionCount = [...selectedIds].filter(
        (candidateId) => verificationStates.get(candidateId) === "available",
      ).length;
      return route.fulfill({
        json: {
          run_id: runId,
          status: "completed",
          candidate_counts: run.candidate_counts,
          items: pageCandidates.map((candidate) => ({
            candidate,
            is_selected: selectedIds.has(candidate.candidate_id),
            fulltext: verificationStates.has(candidate.candidate_id)
              ? fulltextState(
                  candidate.candidate_id,
                  verificationStates.get(candidate.candidate_id)!,
                )
              : null,
          })),
          page: {
            limit: 20,
            total: visibleCandidates.length,
            next_cursor: isFirstAllPage ? "page-2" : null,
          },
          selection: {
            selected_count: selectedIds.size,
            needs_fulltext_count: needsFulltextCount,
            fulltext_in_progress_count: fulltextInProgressCount,
            ready_for_admission_count: readyForAdmissionCount,
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

test("候选审核在跨页、筛选和刷新后保持准备清单，并在核验页部分加入集合", async ({ page }) => {
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

  await page.getByRole("button", { name: "核验任务" }).click();
  await expect(page).toHaveURL(new RegExp(`/workspace/${workspaceId}/verification\\?run=${runId}`));
  await expect(page.getByText("准备清单").first()).toBeVisible();
  await expect(page.getByText("题录与全文核验", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "开始核验" })).toBeEnabled();

  await page.getByRole("button", { name: "开始核验" }).click();
  await expect(page.getByText("正在校验 PDF")).toBeVisible();
  await expect(page.getByText("已通过核验", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /加入待确认集合/ })).toBeEnabled();

  await page.getByRole("button", { name: /加入待确认集合/ }).click();
  await expect(page.getByTestId("verification-admission-dialog")).toContainText(
    "将 1 篇已核验文献加入待确认集合",
  );
  await page
    .getByTestId("verification-admission-dialog")
    .getByRole("button", { name: "确认加入" })
    .click();
  await expect(page.getByText("本次已加入 1 篇文献")).toBeVisible();
  await expect(page.getByRole("button", { name: "待确认集合 1 篇" })).toBeEnabled();
});
