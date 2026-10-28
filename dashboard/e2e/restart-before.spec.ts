import { randomUUID } from "node:crypto";
import { writeFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

test("persists a browser run at the approval boundary", async ({ page }) => {
  const statePath = process.env.AGENTFORGE_RESTART_STATE_PATH;
  if (!statePath) {
    throw new Error("AGENTFORGE_RESTART_STATE_PATH is required");
  }
  const goal = `Restart proof ${randomUUID()}`;

  await page.goto("/");
  await page.getByRole("button", { name: "New run" }).click();
  await page.getByLabel("Goal").fill(goal);
  await page.getByRole("button", { name: "Start run" }).click();

  await expect(page.locator(".state-badge")).toHaveText("Waiting Approval");
  const runId = (await page.locator(".selected-run-id").textContent())?.trim();
  const actionHash = (
    await page.locator(".approval-panel .hash-value").textContent()
  )?.trim();
  expect(runId).toMatch(/^[0-9a-f-]{36}$/);
  expect(actionHash).toMatch(/^[0-9a-f]{64}$/);
  if (!runId || !actionHash) {
    throw new Error("restart proof identifiers were missing");
  }

  writeFileSync(statePath, JSON.stringify({ runId, goal, actionHash }), "utf8");
  console.log(`RESTART_WAITING ${JSON.stringify({ run_id: runId, action_hash: actionHash })}`);
});
