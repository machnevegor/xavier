import { expect, test } from "@playwright/test";
import { signIn } from "./session.ts";

const SECTIONS = [
  { label: "Work", path: "/" },
  { label: "Needs you", path: "/approvals" },
  { label: "Watch", path: "/watch" },
  { label: "Schedules", path: "/schedules" },
  { label: "Code runs", path: "/runs" },
  { label: "Guardrails", path: "/guardrails" },
  { label: "Usage", path: "/usage" },
];

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("the main navigation opens every section", async ({ page }) => {
  await page.goto("/");
  const navigation = page.getByRole("navigation", { name: "Main" });

  for (const { label, path } of SECTIONS) {
    await navigation.getByRole("link", { name: label }).click();

    await expect(page).toHaveURL(path);
    await expect(page).toHaveTitle(`${label} | Xavier`);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  }
});

test("an unknown page shows the not-found state", async ({ page }) => {
  await page.goto("/no-such-page");

  await expect(page.getByText("There is nothing here")).toBeVisible();
  await expect(page).toHaveTitle("Not found | Xavier");
});

test("a phone can always reach Start task, whatever the agent may use", async ({
  page,
}) => {
  await page.setViewportSize({ height: 740, width: 360 });
  await page.goto("/");
  const settings = page.getByRole("button", { name: /^Settings\./ });

  await settings.click();
  await page.getByRole("checkbox", { name: /^Compute/ }).click();
  await settings.click();
  await page.getByRole("button", { name: "Autonomous" }).click();
  await page
    .getByRole("textbox", { name: "Describe the task" })
    .fill("Compare the pricing pages of three vendors ".repeat(10));

  await expect(settings).toHaveAccessibleName(/Observe, Execute, Compute/);
  const send = await page
    .getByRole("button", { name: "Start task" })
    .boundingBox();
  expect(send).not.toBeNull();
  expect((send?.x ?? 0) + (send?.width ?? 0)).toBeLessThanOrEqual(360);
});

test("a new chat starts with the first-visit defaults, then with the last choices", async ({
  page,
}) => {
  await page.goto("/");
  const settings = page.getByRole("button", { name: /^Settings\./ });
  await expect(settings).toHaveAccessibleName(/Observe, Execute$/);

  await settings.click();
  await page.getByRole("button", { name: "Advanced" }).click();
  const network = page.getByRole("switch", { name: /reach the internet/ });
  await expect(network).toBeChecked();
  await page.getByRole("checkbox", { name: /^Compute/ }).click();
  await network.click();
  await page.getByRole("button", { name: "Read only" }).click();

  await page.reload();
  await expect(settings).toHaveAccessibleName(/Observe, Execute, Compute$/);
  await expect(page.getByRole("button", { name: "Read only" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await settings.click();
  await page.getByRole("button", { name: "Advanced" }).click();
  await expect(network).not.toBeChecked();
});

test("the theme choice survives a reload", async ({ page }) => {
  await page.goto("/");
  const html = page.locator("html");

  await page.getByRole("button", { name: "Dark theme" }).click();
  await expect(html).toHaveClass(/\bdark\b/);

  await page.reload();
  await expect(html).toHaveClass(/\bdark\b/);

  await page.getByRole("button", { name: "Light theme" }).click();
  await expect(html).not.toHaveClass(/\bdark\b/);
});
