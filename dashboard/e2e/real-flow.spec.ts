import { execFileSync } from "node:child_process";
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

test("completes one real approved support report in the browser", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1024 });
  await page.goto("/");
  await page.getByRole("button", { name: "New run" }).click();
  await expect(page.getByLabel("Goal")).toHaveValue("Find recurring support themes");
  await page.getByRole("button", { name: "Start run" }).click();

  await expect(page.locator(".state-badge")).toHaveText("Waiting Approval");
  const runId = (await page.locator(".selected-run-id").textContent())?.trim();
  expect(runId).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  if (!runId) {
    throw new Error("selected run ID was missing");
  }

  await expect(page.getByText("simulated-inbox", { exact: true })).toBeVisible();
  await expect(page.locator(".approval-panel .hash-value")).toHaveText(/^[0-9a-f]{64}$/);
  await expect(page.getByText("text/markdown", { exact: true })).toBeVisible();
  await expect(page.locator(".artifact-panel .hash-value")).toHaveText(/^[0-9a-f]{64}$/);
  await page.screenshot({
    path: resolve(repoRoot, "artifacts", "design-qa-dashboard.png"),
  });

  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.locator(".state-badge")).toHaveText("Succeeded");
  await page.getByText("Report content", { exact: true }).click();
  const report = page.locator(".artifact-content");
  await expect(report).toContainText("# Support Report");
  await expect(report).toContainText("## ticket-001.txt");
  await expect(report).toContainText("## ticket-002.txt");

  const application = queryJson(
    "agentforge",
    `SELECT json_build_object(
      'run_count', (SELECT count(*) FROM runs WHERE id = '${runId}'::uuid),
      'approval_count', (SELECT count(*) FROM approvals WHERE run_id = '${runId}'::uuid),
      'approval_id', (SELECT id::text FROM approvals WHERE run_id = '${runId}'::uuid LIMIT 1),
      'action_hash', (SELECT action_hash FROM approvals WHERE run_id = '${runId}'::uuid LIMIT 1),
      'operation_id', (SELECT operation_id::text FROM approvals WHERE run_id = '${runId}'::uuid LIMIT 1),
      'delivery_count', (SELECT count(*) FROM deliveries WHERE run_id = '${runId}'::uuid),
      'receiver_delivery_id', (SELECT receiver_delivery_id::text FROM deliveries WHERE run_id = '${runId}'::uuid LIMIT 1)
    )::text`,
  );
  expect(application.run_count).toBe(1);
  expect(application.approval_count).toBe(1);
  expect(application.delivery_count).toBe(1);
  expect(application.approval_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(application.action_hash).toMatch(/^[0-9a-f]{64}$/);
  expect(application.operation_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(application.receiver_delivery_id).toMatch(/^[0-9a-f-]{36}$/);

  const inbox = queryJson(
    "agentforge_inbox",
    `SELECT json_build_object(
      'delivery_count', count(*),
      'receiver_delivery_id', max(receiver_delivery_id::text)
    )::text FROM deliveries WHERE operation_id = '${String(application.operation_id)}'::uuid`,
  );
  expect(inbox.delivery_count).toBe(1);
  expect(inbox.receiver_delivery_id).toBe(application.receiver_delivery_id);

  console.log(
    `REAL_FLOW_IDS ${JSON.stringify({ run_id: runId, ...application })}`,
  );
});
