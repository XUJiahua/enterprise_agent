import { test, expect, type Page } from "@playwright/test";

// One serial story on the seed in scripts/product_seed.sql:
// po-100 (900, fully received), po-101 (300, 100 received), both approved.
test.describe.configure({ mode: "serial" });

async function as(page: Page, actor: string) {
  await page.getByLabel("当前体验身份").selectOption(actor);
  await expect(page.getByTestId("purchase-po-100")).toBeVisible();
}
const status = (page: Page, id: string) => page.getByTestId("purchase-" + id).locator(".status");

test("混合批次中有验收不足的采购单时整批不关闭", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/procurement");
  await as(page, "bob");
  await page.getByLabel("选择 po-100").check();
  await page.getByLabel("选择 po-101").check();
  await expect(page.getByRole("button", { name: "批量关闭所选（2）" })).toBeDisabled();
  await page.getByLabel("关闭原因").fill("月末结算");
  await page.getByRole("button", { name: "批量关闭所选（2）" }).click();
  await expect(page.locator(".error")).toContainText("所有采购单保持不变");
  await expect(status(page, "po-100")).toHaveText("已批准");
  await expect(status(page, "po-101")).toHaveText("已批准");
  expect(errors).toEqual([]);
});

test("申请人补登验收后，审批人整批关闭并看到审计", async ({ page }) => {
  await page.goto("/procurement");
  await as(page, "alice");
  await expect(page.getByRole("button", { name: "批量关闭所选（0）" })).toHaveCount(0);
  await page.getByLabel("验收金额 po-101").fill("200");
  await page.getByTestId("purchase-po-101").getByRole("button", { name: "登记验收" }).click();
  await expect(page.locator(".notice")).toContainText("已登记 po-101");
  await expect(page.getByTestId("received-po-101")).toContainText("300");

  await as(page, "bob");
  await page.getByLabel("选择 po-100").check();
  await page.getByLabel("选择 po-101").check();
  await page.getByLabel("关闭原因").fill("验收完成");
  await page.getByRole("button", { name: "批量关闭所选（2）" }).click();
  await expect(page.locator(".notice")).toContainText("已关闭 2 张采购单：po-100、po-101");
  await expect(status(page, "po-100")).toHaveText("已关闭");
  await expect(status(page, "po-101")).toHaveText("已关闭");
  await expect(page.getByLabel("选择 po-100")).toHaveCount(0);
  await expect(page.getByTestId("audit")).toContainText("bob 关闭 po-101 · 原因：验收完成");
  await expect(page.getByTestId("audit")).toContainText("alice 登记验收 po-101");
});

test("创建人收到消费者生成的关闭通知，已关闭单不能再验收", async ({ page }) => {
  await page.goto("/procurement");
  await as(page, "alice");
  const notes = page.getByTestId("notifications");
  await expect(notes).toContainText("采购单 po-100 已关闭", { timeout: 10000 });
  await expect(notes).toContainText("采购单 po-101 已关闭");
  await expect(notes.locator("li")).toHaveCount(2);
  await expect(page.getByLabel("验收金额 po-100")).toHaveCount(0);
  await as(page, "bob");
  await expect(page.getByText("暂无通知")).toBeVisible();
});

test("其他租户看不到这些采购单", async ({ page }) => {
  await page.goto("/procurement");
  await page.getByLabel("当前体验身份").selectOption("erin");
  await expect(page.getByTestId("tenant")).toHaveText("tenant-b");
  await expect(page.getByTestId("purchase-po-200")).toBeVisible();
  await expect(page.getByTestId("purchase-po-100")).toHaveCount(0);
  await expect(page.getByTestId("audit")).not.toContainText("po-10");
});

test("完整流程：申请人创建、审批人批准、申请人验收、审批人关闭", async ({ page }) => {
  await page.goto("/procurement");
  await as(page, "alice");
  await page.getByLabel("采购项目").fill("浏览器全流程");
  await page.getByLabel("申请金额").fill("80");
  await page.getByRole("button", { name: "提交采购单" }).click();
  await expect(page.locator(".notice")).toContainText("已提交采购单 po-");
  const row = page.getByRole("row").filter({ hasText: "浏览器全流程" });
  await expect(row.locator(".status")).toHaveText("待审批");
  const id = (await row.locator("small").textContent())!;

  await as(page, "bob");
  await row.getByRole("button", { name: "批准" }).click();
  await expect(row.locator(".status")).toHaveText("已批准");

  await as(page, "alice");
  await page.getByLabel("验收金额 " + id).fill("80");
  await row.getByRole("button", { name: "登记验收" }).click();
  await expect(page.getByTestId("received-" + id)).toContainText("80");

  await as(page, "bob");
  await page.getByLabel("选择 " + id).check();
  await page.getByLabel("关闭原因").fill("全流程验收");
  await page.getByRole("button", { name: "批量关闭所选（1）" }).click();
  await expect(row.locator(".status")).toHaveText("已关闭");

  await as(page, "alice");
  await expect(page.getByTestId("notifications")).toContainText(`采购单 ${id} 已关闭`, { timeout: 10000 });
});
