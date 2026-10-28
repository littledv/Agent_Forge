import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

const repoRoot = resolve(process.cwd(), "..");

function queryJson(database: string, sql: string): Record<string, unknown> {
  const output = execFileSync(
    "docker",
    [
      "compose",
      "exec",
      "-T",
      "postgresql",
      "psql",
      "-U",
      "temporal",
      "-d",
      database,
      "-Atc",
      sql,
    ],
    { cwd: repoRoot, encoding: "utf8" },
  ).trim();
  return JSON.parse(output) as Record<string, unknown>;
}

test("reopens and completes the same run after application restart", async ({ page }) => {
  const statePath = process.env.AGENTFORGE_RESTART_STATE_PATH;
  if (!statePath) {
    throw new Error("AGENTFORGE_RESTART_STATE_PATH is required");
  }
  const state = JSON.parse(readFileSync(statePath, "utf8")) as {
    runId: string;
    goal: string;
    actionHash: string;
  };

  await page.goto("/");
  await page.getByRole("button", { name: new RegExp(state.goal) }).click();
  await expect(page.locator(".selected-run-id")).toHaveText(state.runId);
  await expect(page.locator(".state-badge")).toHaveText("Waiting Approval");
  await expect(page.locator(".approval-panel .hash-value")).toHaveText(
    state.actionHash,
  );

  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.locator(".state-badge")).toHaveText("Succeeded");
  await page.getByText("Report content", { exact: true }).click();
  await expect(page.locator(".artifact-content")).toContainText("# Support Report");

  const application = queryJson(
    "agentforge",
    `SELECT json_build_object(
      'approval_id', (SELECT id::text FROM approvals WHERE run_id = '${state.runId}'::uuid LIMIT 1),
      'action_hash', (SELECT action_hash FROM approvals WHERE run_id = '${state.runId}'::uuid LIMIT 1),
      'operation_id', (SELECT operation_id::text FROM approvals WHERE run_id = '${state.runId}'::uuid LIMIT 1),
      'delivery_count', (SELECT count(*) FROM deliveries WHERE run_id = '${state.runId}'::uuid),
      'receiver_delivery_id', (SELECT receiver_delivery_id::text FROM deliveries WHERE run_id = '${state.runId}'::uuid LIMIT 1)
    )::text`,
  );
  expect(application.action_hash).toBe(state.actionHash);
  expect(application.delivery_count).toBe(1);

  const inbox = queryJson(
    "agentforge_inbox",
    `SELECT json_build_object('delivery_count', count(*))::text
     FROM deliveries WHERE operation_id = '${String(application.operation_id)}'::uuid`,
  );
  expect(inbox.delivery_count).toBe(1);
  console.log(
    `RESTART_COMPLETE ${JSON.stringify({ run_id: state.runId, ...application })}`,
  );
});
