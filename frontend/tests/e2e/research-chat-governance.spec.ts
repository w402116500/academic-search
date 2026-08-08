import { expect, test, type Page, type Route } from "@playwright/test";

import type { ResearchRun } from "@/api/types";

const workspaceId = "11111111-1111-4111-8111-111111111111";
const conversationId = "22222222-2222-4222-8222-222222222222";
const runId = "33333333-3333-4333-8333-333333333333";
const userMessageId = "44444444-4444-4444-8444-444444444444";
const assistantMessageId = "44444444-4444-4444-8444-444444444445";

const user = {
  id: "55555555-5555-4555-8555-555555555555",
  email: "research-governance@example.test",
  display_name: "研究治理验收",
  created_at: "2026-08-03T00:00:00Z",
};
const workspace = {
  id: workspaceId,
  name: "多篇证据比较",
  description: null,
  research_question: "不同论文中的证据如何比较？",
  status: "active",
  workflow_stage: "researching",
  workflow_stage_display: { label: "证据研究", description: "当前集合可用于研究问答" },
  created_at: "2026-08-03T00:00:00Z",
  updated_at: "2026-08-03T00:00:00Z",
};
const conversation = {
  id: conversationId,
  collection_id: workspaceId,
  title: "比较两组原文证据",
  status: "active",
  created_at: "2026-08-03T00:00:00Z",
  updated_at: "2026-08-03T00:00:00Z",
  message_count: 1,
};

const scopeDocuments = [
  {
    document_id: "66666666-6666-4666-8666-666666666661",
    paper_id: "66666666-6666-4666-8666-666666666671",
    doi: "10.1000/scope-one",
    title: "范围内的第一篇文献",
    authors: [{ given: "Ming", family: "Li" }],
    publication_year: 2024,
    venue: "研究方法学报",
    citation_text: "Li M. 范围内的第一篇文献[J]. 研究方法学报, 2024.",
    tags: [],
    note: null,
    original_filename: "scope-one.pdf",
    byte_size: 2048,
    source_url: "https://example.test/scope-one",
    access_rights: "open",
    added_at: "2026-08-03T00:00:00Z",
    latest_ingestion_run: null,
  },
  {
    document_id: "66666666-6666-4666-8666-666666666662",
    paper_id: "66666666-6666-4666-8666-666666666672",
    doi: "10.1000/scope-two",
    title: "范围内的第二篇文献",
    authors: [{ literal: "证据研究团队" }],
    publication_year: 2025,
    venue: "证据研究",
    citation_text: "证据研究团队. 范围内的第二篇文献[J]. 证据研究, 2025.",
    tags: ["比较"],
    note: "用于核对第二组证据。",
    original_filename: "scope-two.pdf",
    byte_size: 4096,
    source_url: null,
    access_rights: "restricted",
    added_at: "2026-08-03T00:00:00Z",
    latest_ingestion_run: null,
  },
];

function runningRun(cancelRequested = false): ResearchRun {
  return {
    id: runId,
    conversation_id: conversationId,
    collection_id: workspaceId,
    input_message_id: userMessageId,
    output_message_id: null,
    arq_job_id: "research-governance-run",
    mode: "multi_agent",
    status: "running",
    stage: "hybrid_retrieval",
    stage_display: { label: "正在检索原文", description: "正在从当前集合检索受限证据。" },
    model_snapshot: {},
    retrieval_trace: {
      routing: { mode: "multi_agent", reason: "问题需要分别比较多组原文证据。" },
      budget: { model_calls: 3, model_call_limit: 16, tool_calls: 1, tool_call_limit: 6 },
      timing: { current_stage: "hybrid_retrieval" },
    },
    error_code: null,
    error_message: null,
    cancel_requested_at: cancelRequested ? "2026-08-03T00:00:03Z" : null,
    started_at: "2026-08-03T00:00:00Z",
    stage_started_at: "2026-08-03T00:00:01Z",
    finished_at: null,
    created_at: "2026-08-03T00:00:00Z",
    evidences: [],
  };
}

function completedRun(executionMode: "fast_rag" | "strict_research"): ResearchRun {
  return {
    ...runningRun(),
    output_message_id: assistantMessageId,
    mode: executionMode === "fast_rag" ? "single_rag" : "multi_agent",
    status: "completed",
    stage: "completed",
    stage_display: { label: "回答已完成", description: "回答与证据已保存。" },
    finished_at: "2026-08-03T00:00:05Z",
    retrieval_trace: {
      execution_mode: executionMode,
      citation_checked: true,
      claim_verified: executionMode === "strict_research",
      timing: { total_duration_ms: 1200 },
    },
    evidences: [
      {
        id: "77777777-7777-4777-8777-777777777771",
        paper_id: scopeDocuments[0].paper_id,
        title: scopeDocuments[0].title,
        authors: [{ name: "Ming Li" }],
        publication_year: 2024,
        source_url: null,
        citation_excerpt: "第一篇文献中的可引用原文片段。",
        locator_snapshot: { page_start: 4, section_path: ["结果"] },
        is_cited: true,
        display_index: 1,
      },
      {
        id: "77777777-7777-4777-8777-777777777772",
        paper_id: scopeDocuments[1].paper_id,
        title: scopeDocuments[1].title,
        authors: [{ name: "证据研究团队" }],
        publication_year: 2025,
        source_url: null,
        citation_excerpt: "第二篇文献中的候选原文片段。",
        locator_snapshot: { page_start: 8, section_path: ["讨论"] },
        is_cited: false,
        display_index: null,
      },
    ],
  } as unknown as ResearchRun;
}

function completedChat(executionMode: "fast_rag" | "strict_research") {
  return {
    messages: [
      {
        id: userMessageId,
        conversation_id: conversationId,
        role: "user",
        content: "请比较当前集合中的两组证据。",
        status: "completed",
        metadata: {},
        created_at: "2026-08-03T00:00:00Z",
        research_run_id: runId,
      },
      {
        id: assistantMessageId,
        conversation_id: conversationId,
        role: "assistant",
        content: "## 可核查结论\n\n当前证据支持这一结论 [1]。",
        status: "completed",
        metadata: {},
        created_at: "2026-08-03T00:00:05Z",
        research_run_id: runId,
      },
    ],
    runs: [completedRun(executionMode)],
  };
}

function evidenceInsufficientChat() {
  const run = {
    ...runningRun(),
    status: "awaiting_clarification",
    stage: "completed",
    stage_display: { label: "需要补充信息", description: "当前集合缺少可引用证据。" },
    finished_at: "2026-08-03T00:00:05Z",
    evidences: [],
  } as ResearchRun;
  return {
    messages: [
      {
        id: userMessageId,
        conversation_id: conversationId,
        role: "user",
        content: "请比较当前集合中的两组证据。",
        status: "completed",
        metadata: {},
        created_at: "2026-08-03T00:00:00Z",
        research_run_id: runId,
      },
    ],
    runs: [run],
  };
}

async function openResearchChat(
  page: Page,
  documents: typeof scopeDocuments = [],
  chat?: { messages: object[]; runs: ResearchRun[] },
): Promise<{ cancelRequests: () => number }> {
  let cancellationRequested = false;
  let cancelRequestCount = 0;
  await page.addInitScript(() =>
    localStorage.setItem("academic-search.access-token", "research-governance-token"),
  );
  await page.route("http://127.0.0.1:8000/api/v1/**", async (route: Route) => {
    const { pathname } = new URL(route.request().url());
    const method = route.request().method();
    const conversationPath = `/collections/${workspaceId}/conversations/${conversationId}`;
    const runPath = `${conversationPath}/research-runs/${runId}`;
    if (pathname.endsWith("/auth/me")) return route.fulfill({ json: user });
    if (pathname.endsWith(`/collections/${workspaceId}`)) return route.fulfill({ json: workspace });
    if (pathname.endsWith(`/collections/${workspaceId}/documents`)) {
      return route.fulfill({
        json: {
          collection_id: workspaceId,
          documents,
          summary: {
            active_document_count: 1,
            researchable_document_count: 1,
            ingestion_status_counts: { completed: 1 },
          },
        },
      });
    }
    if (pathname.endsWith(`/collections/${workspaceId}/conversations`) && method === "GET") {
      return route.fulfill({ json: [conversation] });
    }
    if (pathname.endsWith(conversationPath) && method === "GET") {
      return route.fulfill({
        json: {
          conversation,
          messages: chat?.messages ?? [
            {
              id: userMessageId,
              conversation_id: conversationId,
              role: "user",
              content: "请比较当前集合中的两组证据。",
              status: "completed",
              metadata: {},
              created_at: "2026-08-03T00:00:00Z",
              research_run_id: runId,
            },
          ],
          runs: chat?.runs ?? [runningRun(cancellationRequested)],
        },
      });
    }
    if (pathname.endsWith(`${runPath}/cancel`) && method === "POST") {
      cancelRequestCount += 1;
      cancellationRequested = true;
      return route.fulfill({ json: runningRun(true) });
    }
    if (pathname.endsWith(`${runPath}/events`)) {
      return route.fulfill({ contentType: "text/event-stream", body: "" });
    }
    return route.fulfill({ status: 404, json: { detail: { message: `未处理请求：${pathname}` } } });
  });
  await page.goto(`/research/${workspaceId}?conversation=${conversationId}`);
  return { cancelRequests: () => cancelRequestCount };
}

test("研究会话显示治理摘要，并将运行中取消转为协作停止状态", async ({ page }) => {
  const api = await openResearchChat(page);

  await expect(page.getByRole("heading", { name: conversation.title })).toBeVisible();
  const runStatus = page.locator(".research-chat-run-status");
  await expect(runStatus).toContainText("问题需要分别比较多组原文证据。");
  await expect(runStatus).toContainText("模型 3/16 次，检索 1/6 次");
  await page.getByRole("button", { name: "请求停止" }).click();

  await expect(page.getByText("已请求停止，正在等待当前调用返回。", { exact: true })).toBeVisible();
  await expect(
    page.getByText("任务会在当前模型或检索调用结束后的安全边界停止，不会生成回答或新的引用证据。"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "请求停止" })).toHaveCount(0);
  expect(api.cancelRequests()).toBe(1);
});

test("研究范围在当前会话内展示文献详情", async ({ page }) => {
  await openResearchChat(page, scopeDocuments);

  await page.getByRole("button", { name: "打开研究范围" }).click();
  const drawer = page.getByRole("dialog", { name: "研究范围文献" });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("范围内的第一篇文献");

  await drawer.getByRole("button", { name: /范围内的第二篇文献/ }).click();
  await expect(drawer).toContainText("证据研究团队. 范围内的第二篇文献[J]. 证据研究, 2025.");
  await expect(drawer).toContainText("用于核对第二组证据。");

  await drawer.getByRole("button", { name: "关闭研究范围", exact: true }).click();
  await expect(drawer).toHaveCount(0);
  await expect(page.getByRole("heading", { name: conversation.title })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "打开研究范围" }).click();
  await drawer.getByRole("button", { name: /范围内的第二篇文献/ }).click();
  await expect(drawer.getByRole("button", { name: "返回文献列表" })).toBeVisible();
  await drawer.getByRole("button", { name: "返回文献列表" }).click();
  await expect(drawer.getByRole("button", { name: /范围内的第二篇文献/ })).toBeVisible();
});

test("快速回答只展示实际引用，并可原地检查和打开文献详情", async ({ page }) => {
  await openResearchChat(page, scopeDocuments, completedChat("fast_rag"));

  await expect(page.getByRole("heading", { name: "可核查结论" })).toBeVisible();
  await expect(page.getByRole("button", { name: "查看引用 1" })).toBeVisible();
  await expect(page.getByText("候选证据", { exact: true })).toHaveCount(0);
  await expect(page.getByText("1 条引用已检查", { exact: true })).toBeVisible();

  const sourceDetails = page.locator(".research-chat-evidence-details").first();
  await expect(sourceDetails).not.toHaveAttribute("open", "");
  await page.getByRole("button", { name: "查看引用 1" }).click();
  await expect(sourceDetails).toHaveAttribute("open", "");

  const evidence = page.locator(
    "#research-evidence-33333333-3333-4333-8333-333333333333-77777777-7777-4777-8777-777777777771",
  );
  await expect(evidence).toHaveClass(/is-highlighted/);
  await expect(evidence).toBeFocused();
  await evidence.getByRole("button", { name: "范围内的第一篇文献" }).click();
  const drawer = page.getByRole("dialog", { name: "研究范围文献" });
  await expect(drawer.getByRole("heading", { name: "范围内的第一篇文献" })).toBeVisible();
});

test("深度研究隔离候选证据", async ({ page }) => {
  await openResearchChat(page, scopeDocuments, completedChat("strict_research"));

  await expect(page.getByText("1 条引用与主张已核验", { exact: true })).toBeVisible();
  await expect(page.getByText("候选证据", { exact: true })).toBeVisible();
});

test("证据不足引导切换模式", async ({ page }) => {
  await openResearchChat(page, scopeDocuments, evidenceInsufficientChat());
  await expect(page.getByText("当前集合的证据不足", { exact: true })).toBeVisible();
  await expect(page.getByText("引用来源", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "切换为深度研究" }).click();
  await expect(page.getByRole("radio", { name: "深度研究" })).toHaveAttribute(
    "aria-checked",
    "true",
  );
});
