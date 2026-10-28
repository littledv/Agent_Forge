import type {
  ApprovalDecision,
  ApprovalSnapshot,
  ArtifactContent,
  CancellationSnapshot,
  ModelProfileDescriptor,
  ModelProfileId,
  ModelProfileSnapshot,
  RunDetailSnapshot,
  RunListItem,
  RunSnapshot,
  RunState,
  SubmitRunInput,
} from "./contracts";

const RUN_STATES = new Set<RunState>([
  "PENDING_START",
  "RUNNING",
  "WAITING_APPROVAL",
  "CANCELLATION_REQUESTED",
  "SUCCEEDED",
  "FAILED",
  "DENIED",
  "CANCELLED",
  "TIMED_OUT",
  "BUDGET_EXHAUSTED",
  "NEEDS_ATTENTION",
]);
const MODEL_PROFILE_IDS = new Set<ModelProfileId>([
  "mock-v1",
  "ollama-qwen3-4b-instruct-q4-v1",
]);
const MODEL_PROVIDERS = new Set(["mock", "ollama"]);
const STEP_STATES = new Set([
  "PENDING",
  "RUNNING",
  "SUCCEEDED",
  "WAITING_APPROVAL",
  "RETRY_SCHEDULED",
  "FAILED",
  "DENIED",
  "CANCELLED",
  "UNCERTAIN",
]);
const ATTEMPT_STATES = new Set(["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]);
const FAILURE_KINDS = new Set(["RETRYABLE", "PERMANENT", "DENIED", "CANCELLED", "UNCERTAIN"]);
const MODEL_USAGE_FIELDS = [
  "model_provider",
  "model_name",
  "model_digest",
  "model_request_count",
  "model_input_tokens",
  "model_output_tokens",
  "model_duration_ms",
  "provider_cost_estimate_usd",
  "input_tokens_estimated",
  "usage_uncertain",
] as const;
const SHA256 = /^[0-9a-f]{64}$/;

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasString(value: Record<string, unknown>, key: string): boolean {
  return typeof value[key] === "string";
}

function hasNullableString(value: Record<string, unknown>, key: string): boolean {
  return value[key] === null || typeof value[key] === "string";
}

function isNullableNonNegativeInteger(value: unknown): boolean {
  return value === null || (Number.isInteger(value) && (value as number) >= 0);
}

function isNullableNonNegativeNumber(value: unknown): boolean {
  return value === null ||
    (typeof value === "number" && Number.isFinite(value) && value >= 0);
}

function isNullableBoolean(value: unknown): boolean {
  return value === null || typeof value === "boolean";
}

function parseModelIdentity(value: Record<string, unknown>): void {
  if (
    !hasString(value, "provider") ||
    !MODEL_PROVIDERS.has(value.provider as string) ||
    !hasString(value, "model_name") ||
    (value.model_name as string).trim().length === 0 ||
    !(value.model_digest === null ||
      (typeof value.model_digest === "string" && SHA256.test(value.model_digest)))
  ) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  if (
    (value.provider === "mock" && value.model_digest !== null) ||
    (value.provider === "ollama" && value.model_digest === null)
  ) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
}

function parseModelProfileDescriptor(value: unknown): ModelProfileDescriptor {
  if (
    !isRecord(value) ||
    !hasString(value, "profile_id") ||
    !MODEL_PROFILE_IDS.has(value.profile_id as ModelProfileId) ||
    !hasString(value, "display_name") ||
    (value.display_name as string).trim().length === 0
  ) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  parseModelIdentity(value);
  const expectedProvider = value.profile_id === "mock-v1" ? "mock" : "ollama";
  if (value.provider !== expectedProvider) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  return value as ModelProfileDescriptor;
}

function parseModelProfileSnapshot(value: unknown): ModelProfileSnapshot {
  if (
    !isRecord(value) ||
    !hasString(value, "profile_id") ||
    !MODEL_PROFILE_IDS.has(value.profile_id as ModelProfileId) ||
    !hasString(value, "task_queue") ||
    (value.task_queue as string).trim().length === 0 ||
    !["context_tokens", "max_output_tokens", "max_prompt_chars", "max_report_chars", "max_repairs"].every(
      (key) => Number.isInteger(value[key]) && (value[key] as number) >= 0,
    )
  ) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  parseModelIdentity(value);
  const expectedProvider = value.profile_id === "mock-v1" ? "mock" : "ollama";
  if (value.provider !== expectedProvider) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  return value as ModelProfileSnapshot;
}

function parseAttemptSnapshot(value: unknown): void {
  if (
    !isRecord(value) ||
    !Number.isInteger(value.attempt_number) ||
    (value.attempt_number as number) <= 0 ||
    !hasString(value, "state") ||
    !ATTEMPT_STATES.has(value.state as string) ||
    !hasNullableString(value, "failure_kind") ||
    !(value.failure_kind === null || FAILURE_KINDS.has(value.failure_kind as string)) ||
    !hasString(value, "created_at") ||
    !hasString(value, "updated_at") ||
    !(value.model_provider === null || value.model_provider === "mock" || value.model_provider === "ollama") ||
    !hasNullableString(value, "model_name") ||
    !(value.model_digest === null ||
      (typeof value.model_digest === "string" && SHA256.test(value.model_digest))) ||
    !isNullableNonNegativeInteger(value.model_request_count) ||
    !isNullableNonNegativeInteger(value.model_input_tokens) ||
    !isNullableNonNegativeInteger(value.model_output_tokens) ||
    !isNullableNonNegativeInteger(value.model_duration_ms) ||
    !isNullableNonNegativeNumber(value.provider_cost_estimate_usd) ||
    !isNullableBoolean(value.input_tokens_estimated) ||
    !isNullableBoolean(value.usage_uncertain)
  ) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  if (MODEL_USAGE_FIELDS.some((field) => value[field] !== null)) {
    parseModelIdentity({
      provider: value.model_provider,
      model_name: value.model_name,
      model_digest: value.model_digest,
    });
  }
}

function parseStepSnapshot(value: unknown): void {
  if (
    !isRecord(value) ||
    !Number.isInteger(value.ordinal) ||
    (value.ordinal as number) < 0 ||
    !hasString(value, "name") ||
    !hasString(value, "state") ||
    !STEP_STATES.has(value.state as string) ||
    !hasString(value, "created_at") ||
    !hasString(value, "updated_at") ||
    !Array.isArray(value.attempts)
  ) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  value.attempts.forEach(parseAttemptSnapshot);
}

function parseRunSnapshot(value: unknown): RunSnapshot {
  if (
    !isRecord(value) ||
    !hasString(value, "run_id") ||
    !hasString(value, "workflow_id") ||
    !hasString(value, "state") ||
    !RUN_STATES.has(value.state as RunState) ||
    !hasString(value, "created_at") ||
    !hasString(value, "updated_at") ||
    !hasNullableString(value, "failure_kind") ||
    !("artifact" in value)
  ) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  return value as RunSnapshot;
}

function parseRunListItem(value: unknown): RunListItem {
  if (
    !isRecord(value) ||
    !hasString(value, "run_id") ||
    !hasString(value, "goal") ||
    !hasString(value, "state") ||
    !RUN_STATES.has(value.state as RunState) ||
    !hasNullableString(value, "failure_kind") ||
    !hasString(value, "created_at") ||
    !hasString(value, "updated_at")
  ) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  return value as RunListItem;
}

function parseRunDetail(value: unknown): RunDetailSnapshot {
  if (
    !isRecord(value) ||
    !hasString(value, "goal") ||
    !Array.isArray(value.ticket_files) ||
    !value.ticket_files.every((item) => typeof item === "string") ||
    typeof value.projection_sequence !== "number" ||
    !Array.isArray(value.steps) ||
    !("model_profile" in value) ||
    !("approval" in value) ||
    typeof value.cancellation_requested !== "boolean" ||
    !("delivery" in value)
  ) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  parseRunSnapshot(value.run);
  parseModelProfileSnapshot(value.model_profile);
  value.steps.forEach(parseStepSnapshot);
  return value as RunDetailSnapshot;
}

function parseApprovalSnapshot(value: unknown): ApprovalSnapshot {
  if (
    !isRecord(value) ||
    !hasString(value, "approval_id") ||
    !hasString(value, "run_id") ||
    !hasString(value, "operation_id") ||
    !hasString(value, "action_hash") ||
    !isRecord(value.action) ||
    !hasString(value, "state") ||
    !hasString(value, "expires_at") ||
    !hasString(value, "created_at") ||
    !hasNullableString(value, "decided_at")
  ) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  return value as ApprovalSnapshot;
}

function parseCancellationSnapshot(value: unknown): CancellationSnapshot {
  if (
    !isRecord(value) ||
    !hasString(value, "request_id") ||
    !hasString(value, "run_id") ||
    !hasString(value, "created_at")
  ) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  return value as CancellationSnapshot;
}

function parseArtifactContent(value: unknown): ArtifactContent {
  if (!isRecord(value) || !isRecord(value.artifact) || typeof value.content !== "string") {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  const artifact = value.artifact;
  if (
    !hasString(artifact, "path") ||
    !hasString(artifact, "sha256") ||
    typeof artifact.size_bytes !== "number" ||
    !hasString(artifact, "media_type")
  ) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  return value as ArtifactContent;
}

function validationDetail(value: unknown): string | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const messages = value.flatMap((issue) => {
    if (!isRecord(issue) || typeof issue.msg !== "string") {
      return [];
    }
    const location = Array.isArray(issue.loc)
      ? issue.loc.filter((part) => typeof part === "string" || typeof part === "number")
      : [];
    const field = location.at(-1);
    return [`${field === undefined ? "Request" : String(field)}: ${issue.msg}`];
  });
  return messages.length > 0 ? messages.join("; ") : null;
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (!isRecord(body)) {
      return "Request failed";
    }
    if (typeof body.detail === "string") {
      return body.detail;
    }
    return validationDetail(body.detail) ?? "Request failed";
  } catch {
    return "Request failed";
  }
}

async function requestJson(url: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    throw new ApiError(response.status, await errorDetail(response));
  }
  return response.json() as Promise<unknown>;
}

export async function listRuns(signal?: AbortSignal): Promise<RunListItem[]> {
  const value = await requestJson("/backend/api/v1/runs?limit=20", { signal });
  if (!Array.isArray(value)) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  return value.map(parseRunListItem);
}

export async function listModelProfiles(
  signal?: AbortSignal,
): Promise<ModelProfileDescriptor[]> {
  const value = await requestJson("/backend/api/v1/model-profiles", { signal });
  if (!Array.isArray(value)) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  const profiles = value.map(parseModelProfileDescriptor);
  const ids = profiles.map((profile) => profile.profile_id);
  if (new Set(ids).size !== ids.length || !ids.includes("mock-v1")) {
    throw new ApiError(502, "AgentForge returned an invalid response");
  }
  return profiles;
}

export async function getRunDetail(
  runId: string,
  signal?: AbortSignal,
): Promise<RunDetailSnapshot> {
  const value = await requestJson(
    `/backend/api/v1/runs/${encodeURIComponent(runId)}/detail`,
    { signal },
  );
  return parseRunDetail(value);
}

export async function submitRun(input: SubmitRunInput): Promise<RunSnapshot> {
  const value = await requestJson("/backend/api/v1/runs", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return parseRunSnapshot(value);
}

export async function decideApproval(
  approvalId: string,
  actionHash: string,
  decision: ApprovalDecision,
): Promise<ApprovalSnapshot> {
  const value = await requestJson(
    `/backend/api/v1/approvals/${encodeURIComponent(approvalId)}/decision`,
    {
      method: "POST",
      body: JSON.stringify({ action_hash: actionHash, decision }),
    },
  );
  return parseApprovalSnapshot(value);
}

export async function cancelRun(runId: string): Promise<CancellationSnapshot> {
  const value = await requestJson(
    `/backend/api/v1/runs/${encodeURIComponent(runId)}/cancel`,
    { method: "POST" },
  );
  return parseCancellationSnapshot(value);
}

export async function getArtifact(runId: string): Promise<ArtifactContent> {
  const value = await requestJson(
    `/backend/api/v1/runs/${encodeURIComponent(runId)}/artifact`,
  );
  return parseArtifactContent(value);
}
