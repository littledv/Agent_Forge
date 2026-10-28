import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

import {
  assertPrivateTextAbsent,
  historyWithDecodedPayloads,
} from "./real-model-privacy";

const repoRoot = resolve(process.cwd(), "..");
const enabled = process.env.AGENTFORGE_REAL_MODEL_TEST === "1";
const modelDigest = process.env.AGENTFORGE_REAL_MODEL_DIGEST ?? "";
const processLogRoot = process.env.AGENTFORGE_PROCESS_LOG_ROOT ?? "";
const ticketSentinel = "Customer cannot export the monthly usage report.";

test.skip(!enabled, "Run only through model_dashboard_stack.ps1");

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
    { cwd: repoRoot, encoding: "utf8", maxBuffer: 16 * 1024 * 1024 },
  ).trim();
  return JSON.parse(output) as Record<string, unknown>;
}

function applicationDump(): string {
  return execFileSync(
    "docker",
    [
      "compose",
      "exec",
      "-T",
      "postgresql",
      "pg_dump",
      "-U",
      "temporal",
      "--data-only",
      "--inserts",
      "agentforge",
    ],
    { cwd: repoRoot, encoding: "utf8", maxBuffer: 32 * 1024 * 1024 },
  );
}

function temporalHistory(workflowId: string): string {
  return execFileSync(
    "docker",
    [
      "compose",
      "run",
      "--rm",
      "--no-deps",
      "--entrypoint",
      "temporal",
      "-e",
      "TEMPORAL_ADDRESS=temporal:7233",
      "temporal-admin-tools",
      "workflow",
      "show",
      "--workflow-id",
      workflowId,
      "--output",
      "json",
    ],
    { cwd: repoRoot, encoding: "utf8", maxBuffer: 32 * 1024 * 1024 },
  );
}

function capturedLogs(): string {
  if (!processLogRoot) {
    throw new Error("AGENTFORGE_PROCESS_LOG_ROOT is required");
  }
  return readdirSync(processLogRoot, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => readFileSync(resolve(entry.parentPath, entry.name), "utf8"))
    .join("\n");
}

test("completes one private, exactly-once local Qwen report", async ({ page }) => {
  expect(modelDigest).toMatch(/^[0-9a-f]{64}$/);
  await page.setViewportSize({ width: 1440, height: 1024 });
  await page.goto("/");
  await page.getByRole("button", { name: "New run" }).click();
  await page
    .getByLabel("Model profile", { exact: true })
    .selectOption("ollama-qwen3-4b-instruct-q4-v1");
  await expect(page.getByText("Provider: Ollama", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Start run" }).click();

  await expect(page.locator(".state-badge")).toHaveText("Waiting Approval");
  const runId = (await page.locator(".selected-run-id").textContent())?.trim();
  expect(runId).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  if (!runId) {
    throw new Error("selected run ID was missing");
  }
  const workflowId = `agentforge/run/${runId}`;

  const profile = page.getByRole("region", { name: "Model profile" });
  await expect(profile).toContainText("ollama-qwen3-4b-instruct-q4-v1");
  await expect(profile).toContainText(`${modelDigest.slice(0, 12)}…`);
  await expect(
    page.getByRole("heading", { name: "Researcher · shard 1" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Researcher · shard 2" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Writer · initial draft" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Reviewer · review 1" }),
  ).toBeVisible();
  const usage = page.getByTestId("model-usage");
  await expect.poll(() => usage.count()).toBeGreaterThanOrEqual(4);
  await expect(usage.first()).toBeVisible();
  await expect(usage.first()).toContainText("Provider charge estimate: $0.000000");
  await expect(page.getByText("Usage may be incomplete after an interrupted model request")).toHaveCount(0);

  const actionHash = (
    await page.locator(".approval-panel .hash-value").textContent()
  )?.trim();
  expect(actionHash).toMatch(/^[0-9a-f]{64}$/);
  await expect(page.getByText("simulated-inbox", { exact: true })).toBeVisible();
  await expect(page.getByText("text/markdown", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();

  await expect(page.locator(".state-badge")).toHaveText("Succeeded");
  await page.getByText("Report content", { exact: true }).click();
  const report = page.locator(".artifact-content");
  const reportText = await report.textContent();
  if (!reportText) {
    throw new Error("verified report content was empty");
  }
  if (
    !reportText.includes("Find recurring support themes") ||
    !reportText.includes("ticket-001.txt") ||
    !reportText.includes("ticket-002.txt")
  ) {
    throw new Error("verified report content did not satisfy the required goal and ticket paths");
  }
  const reportPrefix = reportText.trim().slice(0, 32);
  expect(reportPrefix.length).toBeGreaterThanOrEqual(8);

  const application = queryJson(
    "agentforge",
    `SELECT json_build_object(
      'run_count', (SELECT count(*) FROM runs WHERE id = '${runId}'::uuid),
      'approval_count', (SELECT count(*) FROM approvals WHERE run_id = '${runId}'::uuid),
      'action_hash', (SELECT action_hash FROM approvals WHERE run_id = '${runId}'::uuid LIMIT 1),
      'operation_id', (SELECT operation_id::text FROM approvals WHERE run_id = '${runId}'::uuid LIMIT 1),
      'delivery_count', (SELECT count(*) FROM deliveries WHERE run_id = '${runId}'::uuid),
      'receiver_delivery_id', (SELECT receiver_delivery_id::text FROM deliveries WHERE run_id = '${runId}'::uuid LIMIT 1),
      'research_role_count', (SELECT count(*) FROM steps WHERE run_id = '${runId}'::uuid AND name ~ '^research_shard_[1-4]$'),
      'writer_role_count', (SELECT count(*) FROM steps WHERE run_id = '${runId}'::uuid AND name ~ '^write_report_r[0-2]$'),
      'reviewer_role_count', (SELECT count(*) FROM steps WHERE run_id = '${runId}'::uuid AND name ~ '^review_report_r[0-2]$'),
      'request_count', (SELECT coalesce(sum(a.model_request_count), 0) FROM attempts a JOIN steps s ON s.id = a.step_id WHERE s.run_id = '${runId}'::uuid AND a.model_provider = 'ollama' AND s.name ~ '^(research_shard_[1-4]|write_report_r[0-2]|review_report_r[0-2])$'),
      'input_tokens', (SELECT coalesce(sum(a.model_input_tokens), 0) FROM attempts a JOIN steps s ON s.id = a.step_id WHERE s.run_id = '${runId}'::uuid AND a.model_provider = 'ollama' AND s.name ~ '^(research_shard_[1-4]|write_report_r[0-2]|review_report_r[0-2])$'),
      'output_tokens', (SELECT coalesce(sum(a.model_output_tokens), 0) FROM attempts a JOIN steps s ON s.id = a.step_id WHERE s.run_id = '${runId}'::uuid AND a.model_provider = 'ollama' AND s.name ~ '^(research_shard_[1-4]|write_report_r[0-2]|review_report_r[0-2])$'),
      'duration_ms', (SELECT coalesce(sum(a.model_duration_ms), 0) FROM attempts a JOIN steps s ON s.id = a.step_id WHERE s.run_id = '${runId}'::uuid AND a.model_provider = 'ollama' AND s.name ~ '^(research_shard_[1-4]|write_report_r[0-2]|review_report_r[0-2])$'),
      'provider_cost', (SELECT coalesce(sum(a.provider_cost_estimate_usd), 0) FROM attempts a JOIN steps s ON s.id = a.step_id WHERE s.run_id = '${runId}'::uuid AND a.model_provider = 'ollama' AND s.name ~ '^(research_shard_[1-4]|write_report_r[0-2]|review_report_r[0-2])$'),
      'estimated_count', (SELECT count(*) FROM attempts a JOIN steps s ON s.id = a.step_id WHERE s.run_id = '${runId}'::uuid AND a.model_provider = 'ollama' AND a.input_tokens_estimated),
      'uncertain_count', (SELECT count(*) FROM attempts a JOIN steps s ON s.id = a.step_id WHERE s.run_id = '${runId}'::uuid AND a.model_provider = 'ollama' AND a.usage_uncertain),
      'model_digest', (SELECT min(a.model_digest) FROM attempts a JOIN steps s ON s.id = a.step_id WHERE s.run_id = '${runId}'::uuid AND a.model_provider = 'ollama')
    )::text`,
  );
  expect(application.run_count).toBe(1);
  expect(application.approval_count).toBe(1);
  expect(application.delivery_count).toBe(1);
  expect(application.action_hash).toBe(actionHash);
  expect(application.research_role_count).toBeGreaterThanOrEqual(1);
  expect(application.research_role_count).toBeLessThanOrEqual(4);
  expect(application.writer_role_count).toBeGreaterThanOrEqual(1);
  expect(application.writer_role_count).toBeLessThanOrEqual(3);
  expect(application.reviewer_role_count).toBe(application.writer_role_count);
  expect(application.request_count).toBeGreaterThanOrEqual(1);
  expect(application.input_tokens).toBeGreaterThan(0);
  expect(application.output_tokens).toBeGreaterThan(0);
  expect(application.duration_ms).toBeGreaterThan(0);
  expect(Number(application.provider_cost)).toBe(0);
  expect(application.estimated_count).toBe(0);
  expect(application.uncertain_count).toBe(0);
  expect(application.model_digest).toBe(modelDigest);

  const inbox = queryJson(
    "agentforge_inbox",
    `SELECT json_build_object(
      'delivery_count', count(*),
      'receiver_delivery_id', max(receiver_delivery_id::text),
      'content', max(content)
    )::text FROM deliveries WHERE operation_id = '${String(application.operation_id)}'::uuid`,
  );
  expect(inbox.delivery_count).toBe(1);
  expect(inbox.receiver_delivery_id).toBe(application.receiver_delivery_id);
  if (inbox.content !== reportText) {
    throw new Error("delivered content did not exactly match the approved report");
  }

  const appData = applicationDump();
  const history = historyWithDecodedPayloads(temporalHistory(workflowId));
  const logs = capturedLogs();
  const privateTexts = [ticketSentinel, reportPrefix];
  assertPrivateTextAbsent("the application database", [appData], privateTexts);
  assertPrivateTextAbsent("Temporal history", history, privateTexts);
  assertPrivateTextAbsent("captured process logs", [logs], privateTexts);

  console.log(
    `REAL_MODEL_EVIDENCE ${JSON.stringify({
      run_id: runId,
      workflow_id: workflowId,
      operation_id: application.operation_id,
      model_digest: modelDigest,
      request_count: application.request_count,
      input_tokens: application.input_tokens,
      output_tokens: application.output_tokens,
      duration_ms: application.duration_ms,
    })}`,
  );
});
