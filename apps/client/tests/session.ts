import { expect, type Page } from "@playwright/test";

export const username = process.env.XAVIER_USERNAME ?? "e2e";
export const password = process.env.XAVIER_PASSWORD ?? "e2e-password";

/** Exchange the username and password for the session cookie of the page's browser context. */
export async function signIn(page: Page): Promise<void> {
  const response = await page.request.post("/api/v1/auth/session", {
    data: { data: { password, username } },
  });
  expect(response.status(), await response.text()).toBe(201);
}
