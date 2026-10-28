import { expect, test } from "@playwright/test";

import type { RunDetailSnapshot, StepSnapshot } from "../lib/contracts";
import {
  installBackend,
  runDetail,
  runItem,
} from "./helpers/backend-fixture";

const FIRST_ID = "44444444-4444-4444-8444-444444444444";
const SECOND_ID = "55555555-5555-4555-8555-555555555555";

function attempt(
  attemptNumber: number,
  state: "SUCCEEDED" | "FAILED",
  overrides: Partial<StepSnapshot["attempts"][number]> = {},
): StepSnapshot["attempts"][number] {
  return {
    attempt_number: attemptNumber,
    state,
    failure_kind: state === "FAILED" ? "RETRYABLE" : null,
    created_at: "2026-10-25T03:30:00Z",
    updated_at: "2026-10-25T03:31:00Z",
    model_provider: null,
    model_name: null,
    model_digest: null,
    model_request_count: null,
    model_input_tokens: null,
    model_output_tokens: null,
    model_duration_ms: null,
    provider_cost_estimate_usd: null,
    input_tokens_estimated: null,
    usage_uncertain: null,
    ...overrides,
  };
}

function step(
  ordinal: number,
  name: string,
  state: StepSnapshot["state"],
  attempts: StepSnapshot["attempts"],
): StepSnapshot {
  return {
    ordinal,
    name,
    state,
    created_at: "2026-10-25T03:30:00Z",
    updated_at: "2026-10-25T03:31:00Z",
    attempts,
  };
}

test("renders ordered steps and expands retry history only when useful", async ({
  page,
}) => {
  const detail = runDetail(FIRST_ID, "Trace durable progress", "RUNNING");
  detail.steps = [
    step(0, "Read tickets", "SUCCEEDED", [attempt(1, "SUCCEEDED")]),
    step(1, "Draft report", "RETRY_SCHEDULED", [
      attempt(1, "FAILED"),
      attempt(2, "SUCCEEDED"),
    ]),
  ];
  await installBackend(page, {
    listResponses: [[runItem(FIRST_ID, detail.goal, "RUNNING", detail.run.updated_at)]],
    details: { [FIRST_ID]: detail },
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Trace durable progress/ }).click();

  const steps = page.locator(".timeline-step");
  await expect(steps).toHaveCount(2);
  await expect(steps.nth(0)).toContainText("Read tickets");
  await expect(steps.nth(1)).toContainText("Draft report");
  await expect(steps.nth(0).locator("details")).not.toHaveAttribute("open", "");
  await expect(steps.nth(1).locator("details")).toHaveAttribute("open", "");
  await expect(steps.nth(1)).toContainText("Attempt 1");
  await expect(steps.nth(1)).toContainText("Retryable");
  await expect(steps.nth(1)).toContainText("Attempt 2");
  await expect(steps.nth(0).locator("time").first()).toHaveAttribute(
    "datetime",
    "2026-10-25T03:31:00Z",
  );
  await expect(steps.nth(1).locator("time")).toHaveCount(3);
});

test("labels bounded roles, keeps ordinal order, and hides private role content", async ({
  page,
}) => {
  const detail = runDetail(FIRST_ID, "Trace bounded role usage", "RUNNING");
  const usage = {
    model_provider: "ollama" as const,
    model_name: "qwen3:4b-instruct-2507-q4_K_M",
    model_digest: "d".repeat(64),
    model_request_count: 1,
    model_input_tokens: 120,
    model_output_tokens: 40,
    model_duration_ms: 500,
    provider_cost_estimate_usd: 0,
    input_tokens_estimated: false,
    usage_uncertain: false,
  };
  detail.steps = [
    step(8, "review_report_r1", "SUCCEEDED", [attempt(1, "SUCCEEDED", usage)]),
    step(1, "research_shard_1", "RETRY_SCHEDULED", [
      attempt(1, "FAILED", usage),
      attempt(2, "SUCCEEDED", usage),
    ]),
    step(7, "write_report_r1", "SUCCEEDED", [attempt(1, "SUCCEEDED", usage)]),
    step(5, "write_report_r0", "SUCCEEDED", [attempt(1, "SUCCEEDED", usage)]),
    step(99, "future_role_name", "RUNNING", [attempt(1, "SUCCEEDED")]),
  ];
  Object.assign(detail.steps[0], { feedback: "SENTINEL_PRIVATE_FEEDBACK" });
  Object.assign(detail.steps[1], { note: "SENTINEL_PRIVATE_NOTE" });
  Object.assign(detail.steps[2], { draft: "SENTINEL_PRIVATE_DRAFT" });
  await installBackend(page, {
    listResponses: [[runItem(FIRST_ID, detail.goal, detail.run.state, detail.run.updated_at)]],
    details: { [FIRST_ID]: detail },
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Trace bounded role usage/ }).click();

  const headings = page.locator(".timeline-step h4");
  await expect(headings).toHaveText([
    "Researcher · shard 1",
    "Writer · initial draft",
    "Writer · revision 1",
    "Reviewer · review 2",
    "future_role_name",
  ]);
  await expect(page.locator(".timeline-step").nth(0)).toContainText("2 attempts");
  await expect(page.getByTestId("model-usage")).toHaveCount(5);
  await expect(page.getByText("SENTINEL_PRIVATE_NOTE")).toHaveCount(0);
  await expect(page.getByText("SENTINEL_PRIVATE_DRAFT")).toHaveCount(0);
  await expect(page.getByText("SENTINEL_PRIVATE_FEEDBACK")).toHaveCount(0);
});

test("shows complete local model usage without exposing prompt or response content", async ({
  page,
}) => {
  const detail = runDetail(FIRST_ID, "Inspect bounded model usage", "SUCCEEDED");
  detail.steps = [
    step(1, "Decide action", "SUCCEEDED", [
      attempt(1, "SUCCEEDED", {
        model_provider: "ollama",
        model_name: "qwen3:4b-instruct-2507-q4_K_M",
        model_digest: "d".repeat(64),
        model_request_count: 2,
        model_input_tokens: 1240,
        model_output_tokens: 612,
        model_duration_ms: 8400,
        provider_cost_estimate_usd: 0,
        input_tokens_estimated: false,
        usage_uncertain: false,
      }),
    ]),
  ];
  Object.assign(detail.steps[0].attempts[0], {
    prompt: "SENTINEL_PRIVATE_PROMPT",
    response: "SENTINEL_PRIVATE_RESPONSE",
  });
  await installBackend(page, {
    listResponses: [[runItem(FIRST_ID, detail.goal, detail.run.state, detail.run.updated_at)]],
    details: { [FIRST_ID]: detail },
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Inspect bounded model usage/ }).click();

  const usage = page.getByTestId("model-usage");
  await expect(usage).toBeVisible();
  await expect(usage).toContainText("qwen3:4b-instruct-2507-q4_K_M");
  await expect(usage).toContainText("2 requests");
  await expect(usage).toContainText("1,240 input");
  await expect(usage).toContainText("612 output tokens");
  await expect(usage).toContainText("8.4 s");
  await expect(usage).toContainText("Provider charge estimate: $0.000000");
  await expect(usage).toContainText("excludes hardware and electricity");
  await expect(page.getByText("SENTINEL_PRIVATE_PROMPT")).toHaveCount(0);
  await expect(page.getByText("SENTINEL_PRIVATE_RESPONSE")).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(usage).toBeVisible();
  const usageBounds = await usage.boundingBox();
  expect(usageBounds).not.toBeNull();
  expect(usageBounds!.x + usageBounds!.width).toBeLessThanOrEqual(390);
});

test("labels estimated and interrupted usage as neutral execution evidence", async ({
  page,
}) => {
  const detail = runDetail(FIRST_ID, "Inspect interrupted usage", "RUNNING");
  detail.steps = [
    step(1, "Decide action", "RUNNING", [
      attempt(1, "FAILED", {
        model_provider: "ollama",
        model_name: "qwen3:4b-instruct-2507-q4_K_M",
        model_digest: "d".repeat(64),
        model_request_count: 1,
        model_input_tokens: 77,
        model_output_tokens: null,
        model_duration_ms: null,
        provider_cost_estimate_usd: 0,
        input_tokens_estimated: true,
        usage_uncertain: true,
      }),
    ]),
  ];
  await installBackend(page, {
    listResponses: [[runItem(FIRST_ID, detail.goal, detail.run.state, detail.run.updated_at)]],
    details: { [FIRST_ID]: detail },
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Inspect interrupted usage/ }).click();

  const usage = page.getByTestId("model-usage");
  await expect(usage).toBeVisible();
  await expect(usage).toContainText("77 estimated input");
  await expect(usage).toContainText("unknown output tokens");
  await expect(usage).toContainText("Usage may be incomplete after an interrupted model request");
  await expect(usage).toHaveClass(/usage-uncertain/);
  await expect(usage).not.toHaveClass(/failure/);
});

test("does not invent usage for mock attempts with nullable counters", async ({ page }) => {
  const detail = runDetail(FIRST_ID, "Inspect mock usage", "SUCCEEDED");
  detail.steps = [
    step(1, "Decide action", "SUCCEEDED", [attempt(1, "SUCCEEDED")]),
  ];
  await installBackend(page, {
    listResponses: [[runItem(FIRST_ID, detail.goal, detail.run.state, detail.run.updated_at)]],
    details: { [FIRST_ID]: detail },
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Inspect mock usage/ }).click();

  await expect(page.getByTestId("model-usage")).toHaveCount(0);
  await expect(page.getByText(/0 input|0 output|\$0\.000000/)).toHaveCount(0);
});

test("rejects partial model usage identity at the API boundary", async ({ page }) => {
  const detail = runDetail(FIRST_ID, "Reject malformed usage", "SUCCEEDED");
  detail.steps = [
    step(1, "Decide action", "SUCCEEDED", [
      attempt(1, "SUCCEEDED", {
        model_provider: null,
        model_name: null,
        model_digest: null,
        model_request_count: 1,
        model_input_tokens: 25,
        provider_cost_estimate_usd: 0,
        input_tokens_estimated: true,
        usage_uncertain: true,
      }),
    ]),
  ];
  await installBackend(page, {
    listResponses: [[runItem(FIRST_ID, detail.goal, detail.run.state, detail.run.updated_at)]],
    details: { [FIRST_ID]: detail },
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Reject malformed usage/ }).click();

  await expect(
    page.getByRole("status").filter({
      hasText: "AgentForge returned an invalid response",
    }),
  ).toBeVisible();
  await expect(page.getByTestId("model-usage")).toHaveCount(0);
});

test("stops terminal polling but keeps nonterminal detail and list polling active", async ({
  page,
}) => {
  const terminal = runDetail(FIRST_ID, "Finished report", "SUCCEEDED");
  const running = runDetail(SECOND_ID, "Live report", "RUNNING");
  const backend = await installBackend(page, {
    listResponses: [[
      runItem(FIRST_ID, terminal.goal, "SUCCEEDED", terminal.run.updated_at),
      runItem(SECOND_ID, running.goal, "RUNNING", running.run.updated_at),
    ]],
    details: { [FIRST_ID]: terminal, [SECOND_ID]: running },
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Finished report/ }).click();
  await expect(page.getByRole("heading", { name: "Finished report" })).toBeVisible();
  await page.waitForTimeout(1300);
  expect(backend.detailRequests.filter((id) => id === FIRST_ID)).toHaveLength(1);

  await page.getByRole("button", { name: /Live report/ }).click();
  await expect(page.getByRole("heading", { name: "Live report" })).toBeVisible();
  await expect
    .poll(() => backend.detailRequests.filter((id) => id === SECOND_ID).length)
    .toBeGreaterThanOrEqual(2);
  await expect.poll(() => backend.listRequests).toBeGreaterThanOrEqual(2);
});

test("aborts an old selection and never lets its delayed response win", async ({
  page,
}) => {
  const first = runDetail(FIRST_ID, "Slow older selection", "RUNNING");
  const second = runDetail(SECOND_ID, "Current selection", "RUNNING");
  const backend = await installBackend(page, {
    listResponses: [[
      runItem(FIRST_ID, first.goal, "RUNNING", first.run.updated_at),
      runItem(SECOND_ID, second.goal, "RUNNING", second.run.updated_at),
    ]],
    details: {
      [FIRST_ID]: { delayMs: 900, value: first },
      [SECOND_ID]: second,
    },
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Slow older selection/ }).click();
  await expect.poll(() => backend.detailRequests).toContain(FIRST_ID);
  await page.getByRole("button", { name: /Current selection/ }).click();

  await expect(page.getByRole("heading", { name: "Current selection" })).toBeVisible();
  await page.waitForTimeout(1100);
  await expect(page.getByRole("heading", { name: "Current selection" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Slow older selection" })).toHaveCount(0);
});

test("labels an old nonterminal projection as neutral lag", async ({ page }) => {
  const detail: RunDetailSnapshot = runDetail(FIRST_ID, "Lagging projection", "RUNNING");
  detail.run.updated_at = new Date(Date.now() - 11_000).toISOString();
  await installBackend(page, {
    listResponses: [[runItem(FIRST_ID, detail.goal, "RUNNING", detail.run.updated_at)]],
    details: { [FIRST_ID]: detail },
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Lagging projection/ }).click();

  await expect(page.getByText("Projection may be delayed")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Application projection" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Open Temporal UI" })).toHaveAttribute(
    "href",
    "http://127.0.0.1:8080",
  );
  await expect(page.getByRole("heading", { name: "Failed" })).toHaveCount(0);
  await expect(page.locator(".projection-status")).not.toHaveClass(/failure/);
});

test("manually refreshes a terminal projection", async ({ page }) => {
  const finished = runDetail(FIRST_ID, "Refresh finished report", "SUCCEEDED");
  const refreshed = runDetail(FIRST_ID, finished.goal, "SUCCEEDED");
  refreshed.projection_sequence = 2;
  const backend = await installBackend(page, {
    listResponses: [[runItem(FIRST_ID, finished.goal, "SUCCEEDED", finished.run.updated_at)]],
    details: { [FIRST_ID]: [finished, refreshed] },
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Refresh finished report/ }).click();
  await expect(page.getByText("#1", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Refresh run" }).click();

  await expect(page.getByText("#2", { exact: true })).toBeVisible();
  expect(backend.detailRequests.filter((id) => id === FIRST_ID)).toHaveLength(2);
});

test("shows delivery evidence and operator guidance for uncertain outcomes", async ({
  page,
}) => {
  const detail = runDetail(FIRST_ID, "Reconcile delivery", "NEEDS_ATTENTION");
  detail.delivery = {
    delivery_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    operation_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    artifact_sha256: "c".repeat(64),
    committed_at: "2026-10-25T03:36:00Z",
  };
  await installBackend(page, {
    listResponses: [[runItem(FIRST_ID, detail.goal, detail.run.state, detail.run.updated_at)]],
    details: { [FIRST_ID]: detail },
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Reconcile delivery/ }).click();

  await expect(page.getByRole("heading", { name: "Delivery receipt" })).toBeVisible();
  await expect(page.getByText(detail.delivery.delivery_id, { exact: true })).toBeVisible();
  await expect(page.getByText(detail.delivery.operation_id, { exact: true })).toBeVisible();
  await expect(page.getByText(detail.delivery.artifact_sha256, { exact: true })).toBeVisible();
  await expect(page.getByText(/external result could not be reconciled/i)).toBeVisible();
  await expect(page.getByText(/review Temporal history/i)).toBeVisible();
});
