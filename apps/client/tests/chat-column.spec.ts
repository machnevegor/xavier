import { expect, test } from "@playwright/test";
import { signIn } from "./session.ts";

test("the profile opens in place and the conversation scroll comes back", async ({
  page,
}) => {
  await signIn(page);
  const created = await page.request.post("/api/v1/chats", {
    data: {
      data: {
        text: "Look through this long note and keep every line.\n".repeat(80),
      },
    },
  });
  expect(created.status(), await created.text()).toBe(201);
  const chat = (await created.json()).data as { id: string; title: string };
  const messages = await page.request.get(`/api/v1/chats/${chat.id}/messages`);
  expect(messages.ok()).toBe(true);
  const messageId = (await messages.json()).data[0].id as string;

  await page.setViewportSize({ height: 640, width: 1280 });
  await page.goto(`/chats/${chat.id}?message=${messageId}`);
  await expect(page).toHaveTitle(
    new RegExp(
      `${chat.title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\| Xavier$`,
    ),
  );
  await expect
    .poll(() => page.locator('link[rel="icon"]').getAttribute("href"))
    .toMatch(/^data:image\/svg\+xml/);
  const message = page.locator(`#message-${messageId}`);
  await expect(message).toHaveAttribute("data-highlighted", "true");

  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect
    .poll(() => page.evaluate(() => window.scrollY))
    .toBeGreaterThan(100);
  const scrolled = await page.evaluate(() => window.scrollY);

  await page.getByRole("button", { name: /Chat details/ }).click();
  await expect(page).toHaveURL(/view=profile/);
  await expect(page.getByRole("list", { name: "Conversation" })).toBeHidden();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeLessThan(20);

  await page.getByRole("button", { name: "Back to the conversation" }).click();
  await expect(page).not.toHaveURL(/view=/);
  await expect
    .poll(() => page.evaluate(() => window.scrollY))
    .toBeGreaterThan(scrolled - 40);

  await page.getByRole("button", { name: /Chat details/ }).click();
  await expect(page).toHaveURL(/view=profile/);
  await page.goBack();
  await expect(page).not.toHaveURL(/view=/);
  await expect
    .poll(() => page.evaluate(() => window.scrollY))
    .toBeGreaterThan(100);
});
