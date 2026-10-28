import { defineConfig, devices } from "@playwright/test";

const isRealModelRun = process.env.AGENTFORGE_REAL_MODEL_TEST === "1";

export default defineConfig({
  testDir: "./e2e",
  testMatch:
    process.env.AGENTFORGE_REAL_TEST ??
    (isRealModelRun
      ? "real-model.spec.ts"
      : "real-flow.spec.ts"),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: isRealModelRun ? 900_000 : 120_000,
  expect: {
    timeout: isRealModelRun ? 600_000 : 60_000,
  },
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3000",
    screenshot: isRealModelRun ? "off" : "only-on-failure",
    trace: isRealModelRun ? "off" : "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
});
