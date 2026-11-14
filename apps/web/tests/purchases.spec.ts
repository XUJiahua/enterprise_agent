import { test, expect } from "@playwright/test";

async function ready(page: import("@playwright/test").Page) {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "采购审批工作台" }),
  ).toBeVisible();
  await expect(
    page.getByRole("row").filter({ hasText: "Alice laptop" }),
  ).toBeVisible();
}
test("申请人可创建采购单，不能审批", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await ready(page);
  const title = "浏览器采购-" + Date.now();
  await page.getByLabel("采购项目", { exact: true }).fill(title);
  await page.getByLabel("申请金额").fill("1234");
  await page.getByRole("button", { name: "提交采购单" }).click();
  await expect(page.getByRole("status")).toContainText("采购单已提交");
  const row = page.getByRole("row").filter({ hasText: title });
  await expect(row).toContainText("alice");
  await expect(row).toContainText("待审批");
  await expect(
    row.getByRole("button", { name: "批准", exact: true }),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
});
test("审批人不能自审批，其他审批人可以批准", async ({ page }) => {
  await ready(page);
  await page.getByLabel("当前体验身份").selectOption("bob");
  await expect(page.getByTestId("purchase-a-self")).toContainText("本人提交");
  await expect(
    page
      .getByTestId("purchase-a-self")
      .getByRole("button", { name: "批准", exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("当前体验身份").selectOption("carol");
  const row = page.getByTestId("purchase-a-other");
  await row.getByRole("button", { name: "批准", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("审批已完成");
  await expect(row).toContainText("已批准");
  await expect(row).toContainText("已完成");
  await expect(page.getByRole("button", { name: "提交采购单" })).toBeDisabled();
});
test("切换租户后清除旧记录且不泄露内部字段", async ({ page }) => {
  await ready(page);
  await page.getByLabel("当前体验身份").selectOption("dave");
  await expect(page.getByTestId("tenant")).toHaveText("tenant-b");
  await expect(page.getByTestId("purchase-b-other")).toContainText(
    "Dave laptop",
  );
  await expect(page.getByTestId("purchase-a-own")).toHaveCount(0);
  await expect(page.getByRole("table")).not.toContainText(
    "supplier-bank-account",
  );
  await page.getByLabel("当前体验身份").selectOption("alice");
  await expect(page.getByTestId("purchase-a-own")).toBeVisible();
  await expect(page.getByTestId("purchase-b-other")).toHaveCount(0);
});
test("代理拒绝未知路径和身份，不转发伪造权限", async ({ request }) => {
  expect((await request.get("/api/backend/purchases")).status()).toBe(401);
  expect(
    (
      await request.get("/api/backend/health", {
        headers: { "X-Demo-Identity": "alice" },
      })
    ).status(),
  ).toBe(404);
  const forged = await request.post("/api/backend/purchases/a-own/approve", {
    headers: {
      "X-Demo-Identity": "alice",
      Authorization: "Bearer demo_carol",
      "X-Role": "approver",
      "X-Tenant-ID": "tenant-b",
    },
  });
  expect([403, 404]).toContain(forged.status());
  const privateRecord = await request.get("/api/backend/purchases/b-other", {
    headers: { "X-Demo-Identity": "alice" },
  });
  expect([403, 404]).toContain(privateRecord.status());
});
test("桌面工作台截图", async ({ page }, info) => {
  await ready(page);
  await page.screenshot({
    path: info.outputPath("desktop.png"),
    fullPage: true,
  });
  await info.attach("desktop", {
    path: info.outputPath("desktop.png"),
    contentType: "image/png",
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
test("移动端可创建且页面无横向溢出", async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await ready(page);
  await page.getByLabel("采购项目", { exact: true }).fill("移动端采购");
  await page.getByLabel("申请金额").fill("88");
  await page.getByRole("button", { name: "提交采购单" }).click();
  await expect(page.getByRole("status")).toContainText("采购单已提交");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("mobile.png"),
    fullPage: true,
  });
  await info.attach("mobile", {
    path: info.outputPath("mobile.png"),
    contentType: "image/png",
  });
});
