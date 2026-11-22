import { expect, test } from "@playwright/test";
import { signIn } from "./session.ts";

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("a picked file shows in the composer at once", async ({ page }) => {
  await page.goto("/");

  await page.getByLabel("Attach files").setInputFiles({
    buffer: Buffer.from("name,value\na,1\n"),
    mimeType: "text/csv",
    name: "sample.csv",
  });

  const list = page.getByRole("list", { name: "Attachments" });
  await expect(list.getByText("sample.csv")).toBeVisible();
  await expect(
    list.getByRole("button", { name: "Remove sample.csv" }),
  ).toBeVisible();
});
