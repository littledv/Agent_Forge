export type RunState =
  | "PENDING_START"
  | "RUNNING"
  | "WAITING_APPROVAL"
  | "CANCELLATION_REQUESTED"
  | "SUCCEEDED"
  | "FAILED"
  | "DENIED"
  | "CANCELLED"
  | "TIMED_OUT"
  | "BUDGET_EXHAUSTED"
  | "NEEDS_ATTENTION";

export type StepState =
  | "PENDING"
  | "RUNNING"
  | "SUCCEEDED"
  | "WAITING_APPROVAL"
  | "RETRY_SCHEDULED"
  | "FAILED"
  | "DENIED"
  | "CANCELLED"
  | "UNCERTAIN";

export type AttemptState =
  | "PENDING"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED";

export type FailureKind =
  | "RETRYABLE"
  | "PERMANENT"
  | "DENIED"
  | "CANCELLED"
  | "UNCERTAIN";

export type ApprovalState = "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED";

export type ModelProfileId = "mock-v1" | "ollama-qwen3-4b-instruct-q4-v1";
export type ModelProvider = "mock" | "ollama";

export type ModelProfileDescriptor = {
  profile_id: ModelProfileId;
  display_name: string;
  provider: ModelProvider;
  model_name: string;
  model_digest: string | null;
};

export type ModelProfileSnapshot = {
  profile_id: ModelProfileId;
  provider: ModelProvider;
  model_name: string;
  model_digest: string | null;
  task_queue: string;
  context_tokens: number;
  max_output_tokens: number;
  max_prompt_chars: number;
  max_report_chars: number;
  max_repairs: number;
};

export type ArtifactRef = {
  path: string;
  sha256: string;
  size_bytes: number;
  media_type: string;
};

export type DeliverReportAction = {
  type: "deliver_report";
  target: "simulated-inbox";
  artifact: ArtifactRef;
};

export type ApprovalSnapshot = {
  approval_id: string;
  run_id: string;
  operation_id: string;
  action_hash: string;
  action: DeliverReportAction;
  state: ApprovalState;
  expires_at: string;
  created_at: string;
  decided_at: string | null;
};

export type DeliveryReceipt = {
  delivery_id: string;
  operation_id: string;
  artifact_sha256: string;
  committed_at: string;
};

export type RunSnapshot = {
  run_id: string;
  workflow_id: string;
  state: RunState;
  created_at: string;
  updated_at: string;
  failure_kind: FailureKind | null;
  artifact: ArtifactRef | null;
};

export type RunListItem = {
  run_id: string;
  goal: string;
  state: RunState;
  failure_kind: FailureKind | null;
  created_at: string;
  updated_at: string;
};

export type AttemptSnapshot = {
  attempt_number: number;
  state: AttemptState;
  failure_kind: FailureKind | null;
  created_at: string;
  updated_at: string;
  model_provider: ModelProvider | null;
  model_name: string | null;
  model_digest: string | null;
  model_request_count: number | null;
  model_input_tokens: number | null;
  model_output_tokens: number | null;
  model_duration_ms: number | null;
  provider_cost_estimate_usd: number | null;
  input_tokens_estimated: boolean | null;
  usage_uncertain: boolean | null;
};

export type StepSnapshot = {
  ordinal: number;
  name: string;
  state: StepState;
  created_at: string;
  updated_at: string;
  attempts: AttemptSnapshot[];
};

export type RunDetailSnapshot = {
  run: RunSnapshot;
  goal: string;
  ticket_files: string[];
  model_profile: ModelProfileSnapshot;
  projection_sequence: number;
  steps: StepSnapshot[];
  approval: ApprovalSnapshot | null;
  cancellation_requested: boolean;
  delivery: DeliveryReceipt | null;
};

export type SubmitRunInput = {
  submission_key: string;
  goal: string;
  ticket_files: string[];
  model_profile_id: ModelProfileId;
};

export type ApprovalDecision = "APPROVE" | "REJECT";

export type CancellationSnapshot = {
  request_id: string;
  run_id: string;
  created_at: string;
};

export type ArtifactContent = {
  artifact: ArtifactRef;
  content: string;
};
