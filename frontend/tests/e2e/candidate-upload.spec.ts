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
  citation: {
    status: "ready",
    doi: "10.5555/upload-ui-acceptance",
    url: "https://doi.org/10.5555/upload-ui-acceptance",
  },
};

const requiresUpload = {
  search_run_id: runId,
  candidate_id: candidateId,
  attempt_no: 1,
  status: "requires_upload",
  document: null,
  error: {
    code: "fulltext_requires_upload",
    message: "没有可用的开放获取 PDF，请上传有权处理的文件。",
    retryable: false,
  },
  requested_at: "2026-08-03T00:00:00Z",
  updated_at: "2026-08-03T00:00:00Z",
};

const availableFulltext = {
  ...requiresUpload,
  status: "available",
  document: {
    candidate_id: candidateId,
    doi: candidate.doi,
    source_url: `user-upload://candidate/${candidateId}`,
    staging_object_key: `staging/${candidateId}/acceptance.pdf`,
    original_filename: "authorized.pdf",
    media_type: "application/pdf",
    byte_size: 45,
    sha256: "a".repeat(64),
    origin_kind: "user_upload",
    access_rights: "user_upload",
    acquired_at: "2026-08-03T00:00:03Z",
  },
  error: null,
  updated_at: "2026-08-03T00:00:03Z",
};

async function fulfillRequest(route: Route): Promise<void> {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;
  const fulltextPath = `/collections/${workspaceId}/search-runs/${runId}/candidates/${candidateId}/fulltext`;

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
    return route.fulfill({ json: { candidate, is_selected: true, fulltext: requiresUpload } });
  }
  if (path.endsWith(`${fulltextPath}/upload`) && request.method() === "POST") {
    expect(request.headers()["x-upload-authorized"]).toBe("true");
    expect(request.headers()["content-type"]).toBe("application/pdf");
    return route.fulfill({ json: availableFulltext });
  }
  if (path.endsWith(`/collections/${workspaceId}/documents`)) {
    return route.fulfill({
      json: {
        collection_id: workspaceId,
        documents: [],
        summary: {
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
  await page.addInitScript(() =>
    localStorage.setItem("academic-search.access-token", "mock-token"),
  );
  await page.route("http://127.0.0.1:8000/api/v1/**", fulfillRequest);
  await page.goto(`/workspace/${workspaceId}/paper/${candidateId}?run=${runId}`);
}

test("有权 PDF 上传需确认授权，并回到核验任务交接", async ({ page }) => {
  await openPaperDetail(page);

  await expect(page.getByLabel("上传有权处理的 PDF")).toBeVisible();
  const submit = page.getByRole("button", { name: "上传并核验" });
  await expect(submit).toBeDisabled();

  await page.locator('input[type="file"]').setInputFiles({
    name: "authorized.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.7\nacceptance\n%%EOF"),
  });
  await expect(page.getByText("authorized.pdf", { exact: true })).toBeVisible();
  await expect(submit).toBeDisabled();

  await page.getByLabel("我确认有权处理并上传这篇文献的 PDF。").check();
  await expect(submit).toBeEnabled();
  await submit.click();

  await expect(page.getByRole("button", { name: "前往核验任务加入集合" })).toBeVisible();
});
