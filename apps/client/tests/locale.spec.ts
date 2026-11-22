import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { type BrowserContext, expect, type Page, test } from "@playwright/test";
import { signIn } from "./session.ts";

const origin = process.env.XAVIER_URL ?? "http://localhost:8080";

function watchHydration(page: Page): string[] {
  const warnings: string[] = [];
  page.on("console", (message) => {
    if (/hydration/i.test(message.text())) warnings.push(message.text());
  });
  return warnings;
}

async function sessionCookie(
  context: BrowserContext,
): Promise<{ name: string; value: string }> {
  const cookies = await context.cookies();
  const session = cookies.find((cookie) => cookie.name === "xavier_session");
  expect(session).toBeTruthy();
  return { name: session?.name ?? "", value: session?.value ?? "" };
}

test("russian is detected on sign-in and switching keeps the address", async ({
  browser,
}) => {
  const context = await browser.newContext({ locale: "ru-RU" });
  const page = await context.newPage();
  const warnings = watchHydration(page);

  await page.goto("/sign-in?redirect=%2Fusage#keep");

  await expect(
    page.getByRole("heading", { name: "Вход в Xavier" }),
  ).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "ru");
  await page.getByLabel("Язык").click();
  await page.getByRole("option", { name: "English" }).click();
  await expect(page).toHaveURL(/\/sign-in\?redirect=/);
  const address = new URL(page.url());
  expect(address.searchParams.get("redirect")).toBe("/usage");
  expect(address.hash).toBe("#keep");
  await expect(
    page.getByRole("heading", { name: "Sign in to Xavier" }),
  ).toBeVisible();
  expect(warnings).toEqual([]);
  await context.close();
});

test("a saved language overrides the device and an account change persists", async ({
  browser,
}) => {
  const first = await browser.newContext({ locale: "en-US" });
  const page = await first.newPage();
  await signIn(page);
  const saved = await page.request.patch("/api/v1/auth/me", {
    data: { data: { locale: "ru" } },
  });
  expect(saved.status()).toBe(200);
  await first.addCookies([{ name: "xavier_locale", url: origin, value: "en" }]);

  await page.goto("/account#devices");
  await expect(page.locator("html")).toHaveAttribute("lang", "ru");
  await expect(page).toHaveURL(/\/account#devices/);

  await page.getByLabel("Язык").click();
  await page.getByRole("option", { name: "English" }).click();
  await expect(page).toHaveURL(/\/account#devices/);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");

  const session = await sessionCookie(first);
  await first.close();

  const second = await browser.newContext({ locale: "ru-RU" });
  await second.addCookies([
    { name: session.name, url: origin, value: session.value },
    { name: "xavier_locale", url: origin, value: "ru" },
  ]);
  const again = await second.newPage();
  await again.goto("/account");
  await expect(again.locator("html")).toHaveAttribute("lang", "en");
  await second.close();
});

function createUser(name: string, password: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = execFile(
      "docker",
      [
        "compose",
        "exec",
        "-T",
        "server",
        "xavier",
        "users",
        "create",
        name,
        "--name",
        "Locale",
        "--password-stdin",
      ],
      { cwd: fileURLToPath(new URL("../../..", import.meta.url)) },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error((stderr || stdout || error.message).trim()));
          return;
        }
        resolve();
      },
    );
    child.stdin?.end(`${password}\n`);
  });
}

test("a first sign-in saves the language already on screen", async ({
  browser,
}) => {
  const name = `locale-${Date.now().toString(36)}`;
  const password = "locale-password-1";
  await createUser(name, password);
  const context = await browser.newContext({ locale: "ru-RU" });
  const page = await context.newPage();
  await page.goto("/sign-in?redirect=%2Faccount");
  await page.getByLabel("Имя пользователя").fill(name);
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL("/account");
  const body: { data?: { user?: { locale?: string | null } } } =
    await page.request
      .get("/api/v1/auth/me")
      .then((response) => response.json());
  expect(body.data?.user?.locale).toBe("ru");
  await context.close();
});
