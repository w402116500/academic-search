import { expect, test, type Page, type Route } from "@playwright/test";

import type { ResearchRun } from "@/api/types";

const workspaceId = "11111111-1111-4111-8111-111111111111";
const conversationId = "22222222-2222-4222-8222-222222222222";
const runId = "33333333-3333-4333-8333-333333333333";
const userMessageId = "44444444-4444-4444-8444-444444444444";

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

async function openResearchChat(page: Page): Promise<{ cancelRequests: () => number }> {
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
          documents: [],
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
          runs: [runningRun(cancellationRequested)],
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
