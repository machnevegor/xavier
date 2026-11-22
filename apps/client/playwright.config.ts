import { defineConfig, devices } from "@playwright/test";

// The tests run against a running stack: `docker compose up` behind Traefik by default.
const config = defineConfig({
  forbidOnly: !!process.env.CI,
  globalSetup: "./tests/global-setup.ts",
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], locale: "en-US" },
    },
  ],
  reporter: "list",
  testDir: "tests",
  use: {
    baseURL: process.env.XAVIER_URL ?? "http://localhost:8080",
    trace: "retain-on-failure",
  },
});

export default config;
