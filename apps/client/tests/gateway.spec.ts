import { expect, test } from "@playwright/test";

test("each document carries its own CSP nonce on every script", async ({
  request,
}) => {
  const nonces = new Set<string>();

  for (let attempt = 0; attempt < 2; attempt++) {
    const response = await request.get("/sign-in");
    const policy = response.headers()["content-security-policy"] ?? "";
    const nonce = /'nonce-([^']+)'/.exec(policy)?.[1];
    expect(nonce).toBeTruthy();

    const scripts = (await response.text()).match(/<script\b[^>]*>/g) ?? [];
    expect(scripts.length).toBeGreaterThan(0);
    for (const script of scripts) {
      expect(script).toContain(`nonce="${nonce}"`);
    }
    nonces.add(nonce ?? "");
  }

  expect(nonces.size).toBe(2);
});

test("the API, its documentation and schema are served by the server", async ({
  request,
}) => {
  const health = await request.get("/api/v1/healthz");
  expect(await health.json()).toEqual({ status: "ok" });

  const schema = await request.get("/api/v1/openapi.json");
  expect((await schema.json()).info.title).toBe("Xavier");

  const docs = await request.get("/api/v1/docs");
  expect(await docs.text()).toContain("/api/v1/openapi.json");

  const unknown = await request.get("/api/v1/no-such-endpoint");
  expect(unknown.status()).toBe(404);
  expect(unknown.headers()["content-type"]).toContain("application/json");
  expect(await unknown.json()).toMatchObject({
    message: expect.any(String),
    success: false,
  });
});

test("the gateway serves localhost and 127.0.0.1 but no other host", async ({
  request,
  baseURL,
}) => {
  for (const hostname of ["localhost", "127.0.0.1"]) {
    const origin = new URL(baseURL ?? "");
    origin.hostname = hostname;
    const page = await request.get(new URL("/sign-in", origin).href);
    expect(page.headers()["content-type"]).toContain("text/html");
    const health = await request.get(new URL("/api/v1/healthz", origin).href);
    expect(await health.json()).toEqual({ status: "ok" });
  }

  for (const path of ["/sign-in", "/api/v1/healthz"]) {
    const other = await request.get(path, { headers: { host: "example.com" } });
    expect(other.status()).toBe(404);
  }
});

test("paths under /api outside v1 never reach the client", async ({
  request,
}) => {
  const response = await request.get("/api/v2/chats");

  expect(response.status()).toBe(404);
  expect(response.headers()["content-type"]).not.toContain("text/html");
});
