import { expect, test } from "@playwright/test";

test("未登录访问研究入口会跳转到登录页", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveURL(/\/login\?redirect=\/$/);
  await expect(page.getByRole("heading", { name: "回到研究台" })).toBeVisible();
  await expect(page.getByLabel("邮箱")).toBeVisible();
  await expect(page.getByLabel("密码")).toBeVisible();
  await expect(page.getByRole("button", { name: "登录" })).toBeVisible();
});

test("注册页保留创建账号所需的最小字段", async ({ page }) => {
  await page.goto("/register");

  await expect(page.getByRole("heading", { name: "创建研究账号" })).toBeVisible();
  await expect(page.getByLabel("显示名称")).toBeVisible();
  await expect(page.getByLabel("邮箱")).toBeVisible();
  await expect(page.getByLabel("密码")).toHaveAttribute("minlength", "12");
  await expect(page.getByRole("button", { name: "创建账号" })).toBeVisible();
});
