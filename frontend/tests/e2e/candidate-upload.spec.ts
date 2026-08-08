import { expect, test, type Page, type Route } from "@playwright/test";

const workspaceId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";
const candidateId = "33333333-3333-4333-8333-333333333333";

const user = {
  id: "44444444-4444-4444-8444-444444444444",
  email: "upload-ui@example.test",
  display_name: "上传界面验收",
  created_at: "2026-08-03T00:00:00Z",
};

const workspace = {
  id: workspaceId,
  name: "上传授权验收工作区",
  description: null,
  research_question: "有权处理的 PDF 如何进入待确认集合？",
  status: "active",
  workflow_stage: "screening",
  workflow_stage_display: { label: "文献筛选", description: "审核候选文献" },
  created_at: "2026-08-03T00:00:00Z",
  updated_at: "2026-08-03T00:00:00Z",
};

const run = {
  id: runId,
  collection_id: workspaceId,
  research_plan_id: "55555555-5555-4555-8555-555555555555",
  status: "completed",
  stage: "completed",
  attempt_no: 1,
  provider_summary: {},
  candidate_counts: { candidate_count: 1 },
  error_code: null,
  error_message: null,
  started_at: "2026-08-03T00:00:00Z",
  finished_at: "2026-08-03T00:01:00Z",
  created_at: "2026-08-03T00:00:00Z",
  updated_at: "2026-08-03T00:01:00Z",
};

const candidate = {
  candidate_id: candidateId,
  doi: "10.5555/upload-ui-acceptance",
  title: "Authorized PDF upload acceptance",
  language: "en",
  authors: [{ name: "Upload Acceptance" }],
  abstract: "This candidate is used to verify an authorized PDF upload workflow.",
  published_year: 2026,
  venue: "Acceptance Test Journal",
  document_type: "article",
  citation_counts_by_source: {},
  links: { landing_url: null, open_access_url: null, fulltext_url: null },
  is_open_access: false,
  triage: { included: true, exclusion_reasons: [], warnings: [] },
  relevance_state: "completed",
  relevance_assessment: {
    level: "related",
    study_focus: "用于验证有权处理的 PDF 上传流程。",
    reason: "候选含有已核验 DOI 和题录。",
    helpful_aspect: "可验证上传不会绕过候选审核与后续准入。",
    limitations: [],
    recommendation: "建议完成 PDF 核验后再决定是否加入集合。",
    evidence: [{ source_field: "title", quote: "Authorized PDF upload acceptance" }],
  },
  relevance_error: null,
  pdf_availability: { status: "requires_upload" },
  citation: {
    status: "ready",
    doi: "10.5555/upload-ui-acceptance",
    url: "https://doi.org/10.5555/upload-ui-acceptance",
  },
};

let admitted = false;

async function fulfillRequest(route: Route): Promise<void> {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;
  const method = request.method();
  const selectionPath = `/search-runs/${runId}/candidate-selection`;

  if (path.endsWith("/auth/me")) return route.fulfill({ json: user });
  if (path.endsWith("/collections"))
    return route.fulfill({ json: { items: [workspace], next_cursor: null } });
  if (path.endsWith(`/collections/${workspaceId}`)) return route.fulfill({ json: workspace });
  if (path.endsWith("/search-runs/current")) return route.fulfill({ json: run });
  if (path.endsWith(`/search-runs/${runId}/candidates/${candidateId}/citation`)) {
    return route.fulfill({
      json: { format: "gb_t_7714_2015_numeric", text: "Upload Acceptance. 2026." },
    });
  }
  if (path.endsWith(`/search-runs/${runId}/candidates/${candidateId}`)) {
    return route.fulfill({ json: { candidate, is_selected: false, fulltext: null } });
  }
  if (path.endsWith(selectionPath) && method === "PATCH") {
    expect(request.postDataJSON()).toEqual({ candidate_ids: [candidateId], selected: true });
    return route.fulfill({ json: { run_id: runId, selected_count: 1 } });
  }
  if (path.endsWith(`${selectionPath}/admission`) && method === "POST") {
    admitted = true;
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
  if (path.endsWith(`/collections/${workspaceId}/documents`)) {
    return route.fulfill({
      json: {
        collection_id: workspaceId,
        bibliography_entries: admitted
          ? [
              {
                id: `entry-${candidateId}`,
                collection_id: workspaceId,
                source_search_run_id: runId,
                source_candidate_id: candidateId,
                title: candidate.title,
                authors: candidate.authors,
                doi: candidate.doi,
                venue: candidate.venue,
                publication_year: candidate.published_year,
                citation_status: "ready",
                citation_text: null,
                pdf_status: "requires_upload",
                content_status: "requires_upload",
                paper_id: null,
                document_id: null,
                created_at: "2026-08-03T00:02:00Z",
                updated_at: "2026-08-03T00:02:00Z",
              },
            ]
          : [],
        documents: [],
        summary: {
          bibliography_entry_count: admitted ? 1 : 0,
          active_document_count: 0,
          researchable_document_count: 0,
          ingestion_status_counts: {},
        },
      },
    });
  }
  return route.fulfill({
    status: 404,
    json: { detail: { message: `Unhandled request: ${path}` } },
  });
}

async function openPaperDetail(page: Page): Promise<void> {
  admitted = false;
  await page.addInitScript(() =>
    localStorage.setItem("academic-search.access-token", "mock-token"),
  );
  await page.route("http://127.0.0.1:8000/api/v1/**", fulfillRequest);
  await page.goto(`/workspace/${workspaceId}/paper/${candidateId}?run=${runId}`);
}

test("需上传 PDF 的候选详情可直接加入研究集合并保留书目", async ({ page }) => {
  await openPaperDetail(page);

  await expect(page.getByText("需上传 PDF", { exact: true })).toBeVisible();
  await expect(page.getByLabel("上传有权处理的 PDF")).not.toBeVisible();
  await expect(page.getByRole("button", { name: "上传并核验" })).not.toBeVisible();

  await page.getByRole("button", { name: "加入研究集合" }).click();

  await expect(page).toHaveURL(new RegExp(`/workspace/${workspaceId}/collection`));
  await expect(page.locator(".collection-summary > div").nth(0)).toContainText("1");
  await expect(page.locator(".collection-summary > div").nth(2)).toContainText("1");
  await expect(page.getByText(candidate.title)).toBeVisible();
  await expect(page.getByText("需上传 PDF").first()).toBeVisible();
});
