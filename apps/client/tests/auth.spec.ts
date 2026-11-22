import { expect, test } from "@playwright/test";
import { password, signIn, username } from "./session.ts";

test("an anonymous visitor signs in and returns to the requested page", async ({
  page,
}) => {
  await page.goto("/usage");
  await expect(page).toHaveURL(/\/sign-in\?redirect=/);

  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL("/usage");
  await expect(
    page.getByRole("heading", { level: 1, name: "Usage" }),
  ).toBeVisible();
});

test("a wrong password is rejected", async ({ page }) => {
  await page.goto("/sign-in");

  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(
    page.getByText("That username or password was not accepted."),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/sign-in/);
});

test("signing out ends the session", async ({ page }) => {
  await signIn(page);
  await page.goto("/");

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/sign-in/);

  await page.goto("/");
  await expect(page).toHaveURL(/\/sign-in/);
});
