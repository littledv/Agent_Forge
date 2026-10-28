import { expect, test } from "@playwright/test";

import {
  installBackend,
  mockProfile,
  ollamaProfile,
  runDetail,
  runItem,
  runSnapshot,
} from "./helpers/backend-fixture";

const NEWEST_ID = "11111111-1111-4111-8111-111111111111";
const OLDER_ID = "22222222-2222-4222-8222-222222222222";
const SUBMITTED_ID = "33333333-3333-4333-8333-333333333333";

test("loads only enabled profiles and defaults to the deterministic mock", async ({
  page,
}) => {
  await installBackend(page, { profileResponses: [[mockProfile()]] });

  await page.goto("/");
  await page.getByRole("button", { name: "New run" }).click();

  const profile = page.getByLabel("Model profile", { exact: true });
  await expect(profile).toHaveValue("mock-v1");
  await expect(profile.getByRole("option")).toHaveCount(1);
  await expect(profile.getByRole("option", { name: "Deterministic mock" })).toHaveCount(1);
  await expect(page.getByText("Local Qwen3 4B", { exact: true })).toHaveCount(0);
});

test("submits the explicitly selected enabled profile", async ({ page }) => {
  const submitted = runSnapshot(SUBMITTED_ID, "PENDING_START");
  const backend = await installBackend(page, {
    profileResponses: [[mockProfile(), ollamaProfile()]],
    submitOutcomes: [submitted],
    details: {
      [SUBMITTED_ID]: runDetail(SUBMITTED_ID, "Find recurring support themes", "PENDING_START"),
    },
  });

  await page.goto("/");
  await page.getByRole("button", { name: "New run" }).click();
  await page.getByLabel("Model profile", { exact: true }).selectOption(
    "ollama-qwen3-4b-instruct-q4-v1",
  );

  await expect(page.getByText("Provider: Ollama", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Model: qwen3:4b-instruct-2507-q4_K_M", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Start run" }).click();

  await expect(page.getByText(SUBMITTED_ID, { exact: true })).toBeVisible();
  expect(backend.submissions[0].model_profile_id).toBe(
    "ollama-qwen3-4b-instruct-q4-v1",
  );
});

test("returns to mock when a profile refresh disables the selection", async ({
  page,
}) => {
  const backend = await installBackend(page, {
    profileResponses: [[mockProfile(), ollamaProfile()], [mockProfile()]],
  });

  await page.goto("/");
  await page.getByRole("button", { name: "New run" }).click();
  const profile = page.getByLabel("Model profile", { exact: true });
  await profile.selectOption("ollama-qwen3-4b-instruct-q4-v1");
  await page.getByRole("button", { name: "Refresh model profiles" }).click();

  await expect(profile).toHaveValue("mock-v1");
  await expect(profile.getByRole("option")).toHaveCount(1);
  expect(backend.profileRequests).toBe(2);
});

test("blocks submission while profiles load or when loading fails", async ({ page }) => {
  const backend = await installBackend(page, {
    profileResponses: [
      { delayMs: 350, body: [mockProfile()] },
      { status: 503, body: { detail: "profiles temporarily unavailable" } },
    ],
  });

  await page.goto("/");
  await page.getByRole("button", { name: "New run" }).click();
  const start = page.getByRole("button", { name: /Loading profiles|Start run/ });
  await expect(start).toBeDisabled();
  await expect(page.getByLabel("Model profile", { exact: true })).toBeDisabled();
  expect(backend.submissions).toEqual([]);

  await expect(page.getByLabel("Model profile", { exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Refresh model profiles" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "profiles temporarily unavailable" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Start run" })).toBeDisabled();
  expect(backend.submissions).toEqual([]);
});

test("keeps the labeled profile control usable on a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installBackend(page, {
    profileResponses: [[mockProfile(), ollamaProfile()]],
  });

  await page.goto("/");
  await page.getByRole("button", { name: "New run" }).click();
  const profile = page.getByLabel("Model profile", { exact: true });
  await profile.focus();
  await page.keyboard.press("End");
  await expect(profile).toHaveValue("ollama-qwen3-4b-instruct-q4-v1");
  const bounds = await page.locator(".model-profile-field").boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
});

test("renders recent runs in API order and selects a run", async ({ page }) => {
  const newest = runItem(
    NEWEST_ID,
    "Find recurring support themes",
    "WAITING_APPROVAL",
    "2026-10-25T03:40:00Z",
  );
  const older = runItem(
    OLDER_ID,
    "Triage high-priority tickets",
    "SUCCEEDED",
    "2026-10-25T02:40:00Z",
  );
  const backend = await installBackend(page, {
    listResponses: [[newest, older]],
    details: {
      [OLDER_ID]: runDetail(OLDER_ID, older.goal, "SUCCEEDED"),
    },
  });

  await page.goto("/");

  const rows = page.getByRole("button", { name: /support themes|high-priority/ });
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText("Find recurring support themes");
  await expect(rows.nth(1)).toContainText("Triage high-priority tickets");

  await rows.nth(1).click();
  await expect(
    page.getByRole("heading", { name: "Triage high-priority tickets" }),
  ).toBeVisible();
  await expect(page.getByText(OLDER_ID, { exact: true })).toBeVisible();
  expect(backend.detailRequests).toEqual([OLDER_ID]);
});

test("reuses one submission key across network retries then selects success", async ({
  page,
}) => {
  const submitted = runSnapshot(SUBMITTED_ID, "PENDING_START");
  const backend = await installBackend(page, {
    listResponses: [[]],
    submitOutcomes: ["abort", "abort", submitted],
    details: {
      [SUBMITTED_ID]: runDetail(
        SUBMITTED_ID,
        "Find recurring support themes",
        "PENDING_START",
      ),
    },
  });

  await page.goto("/");
  await page.getByRole("button", { name: "New run" }).click();

  await expect(page.getByLabel("Goal")).toHaveValue("Find recurring support themes");
  await expect(page.getByText("ticket-001.txt")).toBeVisible();
  await expect(page.getByText("ticket-002.txt")).toBeVisible();

  const start = page.getByRole("button", { name: "Start run" });
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await start.click();
    await expect(
      page.getByRole("alert").filter({
        hasText: "Could not reach AgentForge. Try again.",
      }),
    ).toBeVisible();
    await expect(start).toBeEnabled();
  }
  await start.click();

  await expect(
    page.getByRole("heading", { name: "Find recurring support themes" }),
  ).toBeVisible();
  await expect(page.getByText(SUBMITTED_ID, { exact: true })).toBeVisible();
  expect(backend.submissions).toHaveLength(3);
  const submissionKeys = backend.submissions.map((request) => request.submission_key);
  expect(new Set(submissionKeys)).toEqual(new Set([submissionKeys[0]]));
  expect(submissionKeys[0]).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
});

test("clears stale selection and refreshes recent runs after detail 404", async ({
  page,
}) => {
  const stale = runItem(
    OLDER_ID,
    "A run removed elsewhere",
    "RUNNING",
    "2026-10-25T03:40:00Z",
  );
  const backend = await installBackend(page, {
    listResponses: [[stale], []],
    details: { [OLDER_ID]: "not-found" },
  });

  await page.goto("/");
  await page.getByRole("button", { name: /A run removed elsewhere/ }).click();

  await expect(page.getByRole("status")).toHaveText(
    "That run is no longer available",
  );
  await expect(page.getByRole("heading", { name: "No run selected" })).toBeVisible();
  await expect(page.getByText("No recent runs yet")).toBeVisible();
  expect(backend.listRequests).toBe(2);
});
