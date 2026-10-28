import type { Page, Route } from "@playwright/test";

import type {
  ApprovalSnapshot,
  ArtifactContent,
  CancellationSnapshot,
  RunDetailSnapshot,
  RunListItem,
  RunSnapshot,
  SubmitRunInput,
} from "../../lib/contracts";

type ErrorResponse = {
  status: number;
  body: { detail: string };
  delayMs?: number;
};

type HttpOutcome<T> =
  | { status: number; body: T; delayMs?: number }
  | ErrorResponse;

type SubmitOutcome = "abort" | RunSnapshot | ErrorResponse;
export type ModelProfileDescriptorFixture = {
  profile_id: "mock-v1" | "ollama-qwen3-4b-instruct-q4-v1";
  display_name: string;
  provider: "mock" | "ollama";
  model_name: string;
  model_digest: string | null;
};
type ProfileOutcome =
  | ModelProfileDescriptorFixture[]
  | ErrorResponse
  | { delayMs: number; body: ModelProfileDescriptorFixture[] };
type DetailValue = RunDetailSnapshot | "not-found" | ErrorResponse;
type DetailOutcome =
  | DetailValue
  | { delayMs: number; value: DetailValue };

export type BackendOptions = {
  profileResponses?: ProfileOutcome[];
  listResponses?: RunListItem[][];
  details?: Record<string, DetailOutcome | DetailOutcome[]>;
  submitOutcomes?: SubmitOutcome[];
  approvalOutcomes?: HttpOutcome<ApprovalSnapshot>[];
  cancellationOutcomes?: HttpOutcome<CancellationSnapshot>[];
  artifacts?: Record<
    string,
    HttpOutcome<ArtifactContent> | HttpOutcome<ArtifactContent>[]
  >;
};

export type BackendState = {
  profileRequests: number;
  listRequests: number;
  detailRequests: string[];
  submissions: SubmitRunInput[];
  approvalDecisions: Array<{
    approvalId: string;
    actionHash: string;
    decision: "APPROVE" | "REJECT";
  }>;
  cancellationRequests: string[];
  artifactRequests: string[];
};

function isErrorResponse(value: unknown): value is ErrorResponse {
  return value !== null && typeof value === "object" && "status" in value && "body" in value;
}

function isDelayedDetail(
  value: DetailOutcome,
): value is { delayMs: number; value: DetailValue } {
  return typeof value === "object" && "delayMs" in value && "value" in value;
}

function wait(delayMs: number) {
  return new Promise((resolve) => setTimeout(resolve, delayMs));
}

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

export async function installBackend(
  page: Page,
  options: BackendOptions = {},
): Promise<BackendState> {
  const state: BackendState = {
    profileRequests: 0,
    listRequests: 0,
    detailRequests: [],
    submissions: [],
    approvalDecisions: [],
    cancellationRequests: [],
    artifactRequests: [],
  };
  const profileResponses = options.profileResponses ?? [[mockProfile()]];
  const listResponses = options.listResponses ?? [[]];
  const submitOutcomes = [...(options.submitOutcomes ?? [])];
  const approvalOutcomes = [...(options.approvalOutcomes ?? [])];
  const cancellationOutcomes = [...(options.cancellationOutcomes ?? [])];
  const detailOutcomes = new Map(
    Object.entries(options.details ?? {}).map(([runId, outcomes]) => [
      runId,
      Array.isArray(outcomes) ? [...outcomes] : [outcomes],
    ]),
  );
  const artifactOutcomes = new Map(
    Object.entries(options.artifacts ?? {}).map(([runId, outcomes]) => [
      runId,
      Array.isArray(outcomes) ? [...outcomes] : [outcomes],
    ]),
  );

  await page.route("**/backend/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/backend/, "");

    if (request.method() === "GET" && path === "/api/v1/model-profiles") {
      const responseIndex = Math.min(
        state.profileRequests,
        profileResponses.length - 1,
      );
      state.profileRequests += 1;
      const outcome = profileResponses[responseIndex];
      if ("delayMs" in outcome && typeof outcome.delayMs === "number") {
        await wait(outcome.delayMs);
      }
      if (isErrorResponse(outcome)) {
        await fulfillJson(route, outcome.status, outcome.body);
      } else {
        await fulfillJson(route, 200, Array.isArray(outcome) ? outcome : outcome.body);
      }
      return;
    }

    if (request.method() === "GET" && path === "/api/v1/runs") {
      const responseIndex = Math.min(state.listRequests, listResponses.length - 1);
      state.listRequests += 1;
      await fulfillJson(route, 200, listResponses[responseIndex]);
      return;
    }

    const detailMatch = path.match(/^\/api\/v1\/runs\/([^/]+)\/detail$/);
    if (request.method() === "GET" && detailMatch) {
      const runId = detailMatch[1];
      state.detailRequests.push(runId);
      const outcomes = detailOutcomes.get(runId) ?? ["not-found"];
      const nextOutcome = outcomes.length > 1 ? outcomes.shift() : outcomes[0];
      const outcome = nextOutcome ?? "not-found";
      const value = isDelayedDetail(outcome) ? outcome.value : outcome;
      if (isDelayedDetail(outcome)) {
        await wait(outcome.delayMs);
      }
      if (value === "not-found") {
        await fulfillJson(route, 404, { detail: "run not found" });
      } else if (isErrorResponse(value)) {
        await fulfillJson(route, value.status, value.body);
      } else {
        await fulfillJson(route, 200, value);
      }
      return;
    }

    if (request.method() === "POST" && path === "/api/v1/runs") {
      state.submissions.push(request.postDataJSON() as SubmitRunInput);
      const outcome = submitOutcomes.shift();
      if (outcome === "abort") {
        await route.abort("connectionfailed");
      } else if (outcome && isErrorResponse(outcome)) {
        await fulfillJson(route, outcome.status, outcome.body);
      } else if (outcome) {
        await fulfillJson(route, 202, outcome);
      } else {
        await fulfillJson(route, 500, { detail: "fixture exhausted" });
      }
      return;
    }

    const approvalMatch = path.match(/^\/api\/v1\/approvals\/([^/]+)\/decision$/);
    if (request.method() === "POST" && approvalMatch) {
      const body = request.postDataJSON() as {
        action_hash: string;
        decision: "APPROVE" | "REJECT";
      };
      state.approvalDecisions.push({
        approvalId: approvalMatch[1],
        actionHash: body.action_hash,
        decision: body.decision,
      });
      const outcome = approvalOutcomes.shift() ?? {
        status: 500,
        body: { detail: "fixture exhausted" },
      };
      if (outcome.delayMs) {
        await wait(outcome.delayMs);
      }
      await fulfillJson(route, outcome.status, outcome.body);
      return;
    }

    const cancelMatch = path.match(/^\/api\/v1\/runs\/([^/]+)\/cancel$/);
    if (request.method() === "POST" && cancelMatch) {
      state.cancellationRequests.push(cancelMatch[1]);
      const outcome = cancellationOutcomes.shift() ?? {
        status: 500,
        body: { detail: "fixture exhausted" },
      };
      if (outcome.delayMs) {
        await wait(outcome.delayMs);
      }
      await fulfillJson(route, outcome.status, outcome.body);
      return;
    }

    const artifactMatch = path.match(/^\/api\/v1\/runs\/([^/]+)\/artifact$/);
    if (request.method() === "GET" && artifactMatch) {
      const runId = artifactMatch[1];
      state.artifactRequests.push(runId);
      const outcomes = artifactOutcomes.get(runId) ?? [{
        status: 404,
        body: { detail: "artifact not found" },
      }];
      const outcome = outcomes.length > 1 ? outcomes.shift()! : outcomes[0];
      if (outcome.delayMs) {
        await wait(outcome.delayMs);
      }
      await fulfillJson(route, outcome.status, outcome.body);
      return;
    }

    await fulfillJson(route, 404, { detail: "fixture route not found" });
  });

  return state;
}

export function mockProfile(): ModelProfileDescriptorFixture {
  return {
    profile_id: "mock-v1",
    display_name: "Deterministic mock",
    provider: "mock",
    model_name: "deterministic-support-report-v1",
    model_digest: null,
  };
}

export function ollamaProfile(): ModelProfileDescriptorFixture {
  return {
    profile_id: "ollama-qwen3-4b-instruct-q4-v1",
    display_name: "Local Qwen3 4B",
    provider: "ollama",
    model_name: "qwen3:4b-instruct-2507-q4_K_M",
    model_digest: "d".repeat(64),
  };
}

export function mockProfileSnapshot(): RunDetailSnapshot["model_profile"] {
  return {
    profile_id: "mock-v1",
    provider: "mock",
    model_name: "deterministic-support-report-v1",
    model_digest: null,
    task_queue: "agentforge-support-report-v1",
    context_tokens: 0,
    max_output_tokens: 0,
    max_prompt_chars: 0,
    max_report_chars: 0,
    max_repairs: 0,
  };
}

export function ollamaProfileSnapshot(): RunDetailSnapshot["model_profile"] {
  return {
    profile_id: "ollama-qwen3-4b-instruct-q4-v1",
    provider: "ollama",
    model_name: "qwen3:4b-instruct-2507-q4_K_M",
    model_digest: "d".repeat(64),
    task_queue: "agentforge-support-report-ollama-qwen3-4b-v1",
    context_tokens: 8192,
    max_output_tokens: 1024,
    max_prompt_chars: 16384,
    max_report_chars: 8192,
    max_repairs: 2,
  };
}

export function runItem(
  runId: string,
  goal: string,
  state: RunListItem["state"],
  updatedAt: string,
): RunListItem {
  return {
    run_id: runId,
    goal,
    state,
    failure_kind: null,
    created_at: "2026-10-25T03:30:00Z",
    updated_at: updatedAt,
  };
}

export function runSnapshot(runId: string, state: RunSnapshot["state"]): RunSnapshot {
  return {
    run_id: runId,
    workflow_id: `agentforge/run/${runId}`,
    state,
    created_at: "2026-10-25T03:30:00Z",
    updated_at: "2026-10-25T03:31:00Z",
    failure_kind: null,
    artifact: null,
  };
}

export function runDetail(
  runId: string,
  goal: string,
  state: RunSnapshot["state"] = "RUNNING",
): RunDetailSnapshot {
  return {
    run: runSnapshot(runId, state),
    goal,
    ticket_files: ["ticket-001.txt", "ticket-002.txt"],
    model_profile: mockProfileSnapshot(),
    projection_sequence: 1,
    steps: [],
    approval: null,
    cancellation_requested: false,
    delivery: null,
  };
}
