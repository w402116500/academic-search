import { expect, test, type Page, type Route } from "@playwright/test";

const workspaceId = "11111111-1111-4111-8111-111111111111";

const user = {
  id: "33333333-3333-4333-8333-333333333333",
  email: "workspace-delete@example.test",
  display_name: "删除验收",
  created_at: "2026-08-07T00:00:00Z",
};

const workspace = {
  id: workspaceId,
  name: "睡眠质量与大学生学习表现",
  description: null,
  research_question: "睡眠质量如何影响大学生的学习表现？",
  status: "active",
  workflow_stage: "researching",
  workflow_stage_display: { label: "证据研究", description: "集合已经可用于研究" },
  created_at: "2026-08-07T00:00:00Z",
  updated_at: "2026-08-07T00:00:00Z",
};

async function openWorkspace(page: Page, onDelete: (route: Route) => Promise<void>): Promise<void> {
  await page.addInitScript(() =>
    localStorage.setItem("academic-search.access-token", "browser-test-token"),
  );
  await page.route("http://127.0.0.1:8000/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: user });
    if (path.endsWith("/collections")) {
      return route.fulfill({ json: { items: [workspace], next_cursor: null } });
    }
    if (path.endsWith(`/collections/${workspaceId}`)) {
      if (request.method() === "DELETE") return onDelete(route);
      return route.fulfill({ json: workspace });
    }
    if (path.endsWith("/documents")) {
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
    return route.fulfill({ status: 404, json: { detail: { message: `未处理请求：${path}` } } });
  });
  await page.goto(`/workspace/${workspaceId}/collection`);
}

async function openDeletionDialog(page: Page): Promise<void> {
  const entry = page.locator(".side-workspace-entry").filter({ hasText: workspace.name });
  await entry.hover();
  await page.getByRole("button", { name: `删除工作区 ${workspace.name}` }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
}

test("删除当前工作区必须确认，成功后返回研究入口", async ({ page }) => {
  let deleteRequests = 0;
  await openWorkspace(page, async (route) => {
    expect(route.request().method()).toBe("DELETE");
    deleteRequests += 1;
    await route.fulfill({ status: 204 });
  });

  await openDeletionDialog(page);
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText(workspace.name);
  await expect(dialog).toContainText("删除后无法恢复");
  expect(deleteRequests).toBe(0);

  await dialog.getByRole("button", { name: "永久删除" }).click();
  await expect.poll(() => deleteRequests).toBe(1);
  await expect(page).toHaveURL(/\/$/);
});

test("删除失败时保留确认框和错误信息", async ({ page }) => {
  await openWorkspace(page, async (route) => {
    await route.fulfill({
      status: 503,
      json: {
        detail: {
          code: "deletion_cleanup_failed",
          message: "工作区全文文件清理未完成，请稍后重试。",
        },
      },
    });
  });

  await openDeletionDialog(page);
  await page.getByRole("dialog").getByRole("button", { name: "永久删除" }).click();

  await expect(page.getByRole("alert")).toContainText("工作区删除尚未完成，请稍后继续删除。");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/workspace/${workspaceId}/collection$`));
});

test("删除中的工作区在研究入口只能继续删除", async ({ page }) => {
  let deleteRequests = 0;
  const deletingWorkspace = { ...workspace, status: "deleting" };
  await page.addInitScript(() =>
    localStorage.setItem("academic-search.access-token", "browser-test-token"),
  );
  await page.route("http://127.0.0.1:8000/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: user });
    if (path.endsWith("/collections")) {
      return route.fulfill({ json: { items: [deletingWorkspace], next_cursor: null } });
    }
    if (path.endsWith(`/collections/${workspaceId}`) && request.method() === "DELETE") {
      deleteRequests += 1;
      return route.fulfill({ status: 204 });
    }
    return route.fulfill({ status: 404, json: { detail: { message: `未处理请求：${path}` } } });
  });

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "待完成删除" })).toBeVisible();
  await expect(page.getByText(workspace.name)).toBeVisible();
  await page.getByRole("button", { name: "继续删除" }).click();
  await expect.poll(() => deleteRequests).toBe(1);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("侧栏中的删除中工作区不可导航，只能继续删除", async ({ page }) => {
  let deleteRequests = 0;
  const deletingWorkspace = { ...workspace, status: "deleting" };
  await page.addInitScript(() =>
    localStorage.setItem("academic-search.access-token", "browser-test-token"),
  );
  await page.route("http://127.0.0.1:8000/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: user });
    if (path.endsWith("/collections")) {
      return route.fulfill({ json: { items: [deletingWorkspace], next_cursor: null } });
    }
    if (path.endsWith(`/collections/${workspaceId}`)) {
      if (request.method() === "DELETE") {
        deleteRequests += 1;
        return route.fulfill({ status: 204 });
      }
      return route.fulfill({ json: workspace });
    }
    if (path.endsWith("/documents")) {
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
    return route.fulfill({ status: 404, json: { detail: { message: `未处理请求：${path}` } } });
  });

  await page.goto(`/workspace/${workspaceId}/collection`);

  const entry = page.locator(".side-workspace-entry").filter({ hasText: workspace.name });
  await expect(entry.getByRole("link")).toHaveCount(0);
  await entry.getByRole("button", { name: `继续删除工作区 ${workspace.name}` }).click();
  await expect.poll(() => deleteRequests).toBe(1);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
