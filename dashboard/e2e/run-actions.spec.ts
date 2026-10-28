import { expect, test } from "@playwright/test";

import type {
  ApprovalSnapshot,
  ArtifactContent,
  ArtifactRef,
  RunDetailSnapshot,
} from "../lib/contracts";
import {
  installBackend,
  ollamaProfileSnapshot,
  runDetail,
  runItem,
} from "./helpers/backend-fixture";

const RUN_ID = "66666666-6666-4666-8666-666666666666";
const APPROVAL_ID = "77777777-7777-4777-8777-777777777777";
const OPERATION_ID = "88888888-8888-4888-8888-888888888888";
const ACTION_HASH = "a".repeat(64);
const ARTIFACT_HASH = "b".repeat(64);
const SECOND_RUN_ID = "99999999-9999-4999-8999-999999999999";

const artifact: ArtifactRef = {
  path: "runs/verified-report.md",
  sha256: ARTIFACT_HASH,
  size_bytes: 128,
  media_type: "text/markdown",
};

function approval(state: ApprovalSnapshot["state"]): ApprovalSnapshot {
  return {
    approval_id: APPROVAL_ID,
    run_id: RUN_ID,
    operation_id: OPERATION_ID,
    action_hash: ACTION_HASH,
    action: { type: "deliver_report", target: "simulated-inbox", artifact },
    state,
    expires_at: "2026-10-25T04:30:00Z",
    created_at: "2026-10-25T03:30:00Z",
    decided_at: state === "PENDING" ? null : "2026-10-25T03:35:00Z",
  };
}

function actionableDetail(): RunDetailSnapshot {
  const detail = runDetail(RUN_ID, "Review the support report", "WAITING_APPROVAL");
  detail.run.artifact = artifact;
  detail.approval = approval("PENDING");
  return detail;
}

async function openRun(page: import("@playwright/test").Page, goal: string) {
  await page.goto("/");
  await page.getByRole("button", { name: new RegExp(goal) }).click();
  await expect(page.getByRole("heading", { name: goal })).toBeVisible();
}

test("shows the immutable model profile provenance with a short digest", async ({
  page,
}) => {
  const detail = runDetail(RUN_ID, "Inspect model provenance", "SUCCEEDED");
  detail.model_profile = ollamaProfileSnapshot();
  await installBackend(page, {
    listResponses: [[runItem(RUN_ID, detail.goal, detail.run.state, detail.run.updated_at)]],
    details: { [RUN_ID]: detail },
  });

  await openRun(page, detail.goal);

  const profile = page.getByRole("region", { name: "Model profile" });
  await expect(profile).toContainText("ollama-qwen3-4b-instruct-q4-v1");
  await expect(profile).toContainText("Ollama");
  await expect(profile).toContainText("qwen3:4b-instruct-2507-q4_K_M");
  await expect(profile).toContainText("dddddddddddd…");
  await expect(profile).not.toContainText("d".repeat(64));
});

test("shows exact approval metadata and locks every mutation through refresh", async ({
  page,
}) => {
  const pending = actionableDetail();
  const approved = actionableDetail();
  approved.run.state = "SUCCEEDED";
  approved.approval = approval("APPROVED");
  const backend = await installBackend(page, {
    listResponses: [[runItem(RUN_ID, pending.goal, pending.run.state, pending.run.updated_at)]],
    details: {
      [RUN_ID]: [pending, { delayMs: 350, value: approved }],
    },
    approvalOutcomes: [{ status: 202, body: approval("APPROVED"), delayMs: 200 }],
  });

  await openRun(page, pending.goal);
  await expect(page.getByText("simulated-inbox", { exact: true })).toBeVisible();
  await expect(page.getByText(ACTION_HASH, { exact: true })).toBeVisible();
  await expect(page.getByText(artifact.path, { exact: true })).toBeVisible();
  await expect(page.getByText("text/markdown", { exact: true })).toBeVisible();
  await expect(page.getByText("128 bytes", { exact: true })).toBeVisible();
  await expect(page.getByText(ARTIFACT_HASH, { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("button", { name: "Approve" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Reject" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Cancel run" })).toBeDisabled();
  await expect(page.getByText("Approved", { exact: true })).toBeVisible();
  expect(backend.approvalDecisions).toEqual([
    { approvalId: APPROVAL_ID, actionHash: ACTION_HASH, decision: "APPROVE" },
  ]);
});

test("refreshes the durable winner after approval conflict", async ({ page }) => {
  const pending = actionableDetail();
  const winner = actionableDetail();
  winner.approval = approval("APPROVED");
  const backend = await installBackend(page, {
    listResponses: [[runItem(RUN_ID, pending.goal, pending.run.state, pending.run.updated_at)]],
    details: { [RUN_ID]: [pending, winner] },
    approvalOutcomes: [{
      status: 409,
      body: { detail: "approval decision conflicts with durable state" },
    }],
  });

  await openRun(page, pending.goal);
  await page.getByRole("button", { name: "Reject" }).click();

  await expect(page.getByRole("status")).toHaveText(
    "Durable state changed; refreshed the winning decision",
  );
  await expect(page.getByText("Approved", { exact: true })).toBeVisible();
  expect(backend.approvalDecisions[0].decision).toBe("REJECT");
});

test("keeps mutations locked when the durable winner cannot be refreshed", async ({
  page,
}) => {
  const pending = actionableDetail();
  const winner = actionableDetail();
  winner.approval = approval("APPROVED");
  await installBackend(page, {
    listResponses: [[runItem(RUN_ID, pending.goal, pending.run.state, pending.run.updated_at)]],
    details: {
      [RUN_ID]: [
        pending,
        { status: 503, body: { detail: "projection temporarily unavailable" } },
        winner,
      ],
    },
    approvalOutcomes: [{
      status: 409,
      body: { detail: "approval decision conflicts with durable state" },
    }],
  });

  await openRun(page, pending.goal);
  await page.getByRole("button", { name: "Reject" }).click();

  await expect(page.getByRole("status").first()).toContainText(
    "winning projection could not be refreshed",
  );
  await expect(page.getByRole("button", { name: "Approve" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Reject" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Cancel run" })).toBeDisabled();

  await page.getByRole("button", { name: "Refresh run" }).click();
  await expect(page.getByText("Approved", { exact: true })).toBeVisible();
});

test("records a successful rejection and projects the denied state", async ({ page }) => {
  const pending = actionableDetail();
  const denied = actionableDetail();
  denied.run.state = "DENIED";
  denied.approval = approval("REJECTED");
  const backend = await installBackend(page, {
    listResponses: [[runItem(RUN_ID, pending.goal, pending.run.state, pending.run.updated_at)]],
    details: { [RUN_ID]: [pending, denied] },
    approvalOutcomes: [{ status: 202, body: approval("REJECTED") }],
  });

  await openRun(page, pending.goal);
  await page.getByRole("button", { name: "Reject" }).click();

  await expect(page.getByText("Denied", { exact: true })).toBeVisible();
  await expect(page.getByText("Rejected", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Delivery receipt" })).toHaveCount(0);
  expect(backend.approvalDecisions).toEqual([
    { approvalId: APPROVAL_ID, actionHash: ACTION_HASH, decision: "REJECT" },
  ]);
});

test("confirms cancellation and refreshes the winner after conflict", async ({
  page,
}) => {
  const running = runDetail(RUN_ID, "Cancel safely", "RUNNING");
  const winner = runDetail(RUN_ID, running.goal, "SUCCEEDED");
  const backend = await installBackend(page, {
    listResponses: [[runItem(RUN_ID, running.goal, running.run.state, running.run.updated_at)]],
    details: { [RUN_ID]: [running, winner] },
    cancellationOutcomes: [{
      status: 409,
      body: { detail: "run cannot be cancelled in its durable state" },
    }],
  });

  await openRun(page, running.goal);
  await page.getByRole("button", { name: "Cancel run" }).click();
  await expect(page.getByText("Committed delivery cannot be undone.")).toBeVisible();
  await page.getByRole("button", { name: "Confirm cancellation" }).click();

  await expect(page.getByRole("status")).toHaveText(
    "Durable state changed; refreshed the winning decision",
  );
  await expect(page.getByText("Succeeded", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel run" })).toHaveCount(0);
  expect(backend.cancellationRequests).toEqual([RUN_ID]);
});

test("records a cancellation request without claiming the run is cancelled", async ({
  page,
}) => {
  const running = runDetail(RUN_ID, "Request cancellation", "RUNNING");
  const requested = runDetail(RUN_ID, running.goal, "RUNNING");
  requested.cancellation_requested = true;
  const backend = await installBackend(page, {
    listResponses: [[runItem(RUN_ID, running.goal, running.run.state, running.run.updated_at)]],
    details: { [RUN_ID]: [running, requested] },
    cancellationOutcomes: [{
      status: 202,
      body: {
        request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        run_id: RUN_ID,
        created_at: "2026-10-25T03:34:00Z",
      },
    }],
  });

  await openRun(page, running.goal);
  await page.getByRole("button", { name: "Cancel run" }).click();
  await expect(page.getByText(`Cancel ${running.goal}?`, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Confirm cancellation" }).click();

  await expect(page.getByText("Cancellation requested", { exact: true })).toBeVisible();
  await expect(page.getByRole("main").getByText("Running", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel run" })).toHaveCount(0);
  expect(backend.cancellationRequests).toEqual([RUN_ID]);
});

test("resets cancellation confirmation when another run is selected", async ({ page }) => {
  const first = runDetail(RUN_ID, "First cancellable run", "RUNNING");
  const second = runDetail(SECOND_RUN_ID, "Second cancellable run", "RUNNING");
  const backend = await installBackend(page, {
    listResponses: [[
      runItem(RUN_ID, first.goal, first.run.state, first.run.updated_at),
      runItem(SECOND_RUN_ID, second.goal, second.run.state, second.run.updated_at),
    ]],
    details: { [RUN_ID]: first, [SECOND_RUN_ID]: second },
  });

  await openRun(page, first.goal);
  await page.getByRole("button", { name: "Cancel run" }).click();
  await page.getByRole("button", { name: new RegExp(second.goal) }).click();
  await expect(page.getByRole("heading", { name: second.goal })).toBeVisible();

  await expect(page.getByRole("button", { name: "Confirm cancellation" })).toHaveCount(0);
  await page.getByRole("button", { name: "Cancel run" }).click();
  await expect(page.getByText(`Cancel ${second.goal}?`, { exact: true })).toBeVisible();
  expect(backend.cancellationRequests).toEqual([]);
});

test("loads verified report text once and renders markup literally", async ({ page }) => {
  const detail = runDetail(RUN_ID, "Inspect the report", "SUCCEEDED");
  detail.run.artifact = artifact;
  const content = "<script>alert('no')</script>\n[link](https://example.com)\n<b>literal</b>";
  const response: ArtifactContent = { artifact, content };
  const backend = await installBackend(page, {
    listResponses: [[runItem(RUN_ID, detail.goal, detail.run.state, detail.run.updated_at)]],
    details: { [RUN_ID]: detail },
    artifacts: { [RUN_ID]: { status: 200, body: response } },
  });

  await openRun(page, detail.goal);
  await expect(page.getByText(artifact.path, { exact: true })).toBeVisible();
  expect(backend.artifactRequests).toHaveLength(0);

  const reportToggle = page.getByText("Report content", { exact: true });
  await reportToggle.click();
  await expect(page.locator(".artifact-content")).toHaveText(content);
  await expect(page.locator(".artifact-content script")).toHaveCount(0);
  await expect(page.locator(".artifact-content a")).toHaveCount(0);
  await reportToggle.click();
  await reportToggle.click();
  await expect(page.locator(".artifact-content")).toHaveText(content);
  expect(backend.artifactRequests).toEqual([RUN_ID]);
});

test("sanitizes artifact verification failures", async ({ page }) => {
  const detail = runDetail(RUN_ID, "Inspect a damaged report", "SUCCEEDED");
  detail.run.artifact = artifact;
  await installBackend(page, {
    listResponses: [[runItem(RUN_ID, detail.goal, detail.run.state, detail.run.updated_at)]],
    details: { [RUN_ID]: detail },
    artifacts: {
      [RUN_ID]: {
        status: 409,
        body: { detail: "artifact could not be verified" },
      },
    },
  });

  await openRun(page, detail.goal);
  await page.getByText("Report content", { exact: true }).click();
  await expect(page.getByText("Artifact could not be verified", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry report content" })).toHaveCount(0);
  await expect(page.getByText(/checksum|filesystem|secret/i)).toHaveCount(0);
});

test("retries a transient artifact read without weakening verification", async ({ page }) => {
  const detail = runDetail(RUN_ID, "Retry report inspection", "SUCCEEDED");
  detail.run.artifact = artifact;
  const response: ArtifactContent = { artifact, content: "Verified after retry" };
  const backend = await installBackend(page, {
    listResponses: [[runItem(RUN_ID, detail.goal, detail.run.state, detail.run.updated_at)]],
    details: { [RUN_ID]: detail },
    artifacts: {
      [RUN_ID]: [
        { status: 503, body: { detail: "storage temporarily unavailable" } },
        { status: 200, body: response },
      ],
    },
  });

  await openRun(page, detail.goal);
  await page.getByText("Report content", { exact: true }).click();
  await expect(
    page.getByText("Report content is temporarily unavailable", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Retry report content" }).click();

  await expect(page.locator(".artifact-content")).toHaveText(response.content);
  expect(backend.artifactRequests).toEqual([RUN_ID, RUN_ID]);
});
