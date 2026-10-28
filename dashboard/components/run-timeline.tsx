import { Check, Circle, RotateCcw } from "lucide-react";

import type { AttemptSnapshot, StepSnapshot } from "../lib/contracts";
import { formatState, formatUpdatedAt } from "../lib/format";

const STEP_LABELS: Readonly<Record<string, string>> = {
  read_tickets: "Read tickets",
  decide_action: "Decide action",
  publish_report: "Publish report",
  publish_reviewed_report: "Publish reviewed report",
  create_delivery_approval: "Request delivery approval",
  resolve_delivery_gate: "Check delivery approval",
  deliver_report: "Deliver report",
};

export function formatStepLabel(name: string): string {
  const known = STEP_LABELS[name];
  if (known) {
    return known;
  }
  const researcher = /^research_shard_([1-4])$/.exec(name);
  if (researcher) {
    return `Researcher · shard ${researcher[1]}`;
  }
  const writer = /^write_report_r([0-2])$/.exec(name);
  if (writer) {
    const revision = Number(writer[1]);
    return revision === 0
      ? "Writer · initial draft"
      : `Writer · revision ${revision}`;
  }
  const reviewer = /^review_report_r([0-2])$/.exec(name);
  if (reviewer) {
    return `Reviewer · review ${Number(reviewer[1]) + 1}`;
  }
  return name;
}

function StepIcon({ state }: { state: StepSnapshot["state"] }) {
  if (state === "SUCCEEDED") {
    return <Check size={17} aria-hidden="true" />;
  }
  if (state === "RETRY_SCHEDULED") {
    return <RotateCcw size={16} aria-hidden="true" />;
  }
  return <Circle size={13} aria-hidden="true" />;
}

function formatCount(value: number | null): string {
  return value === null ? "unknown" : value.toLocaleString("en-US");
}

function formatDuration(durationMs: number | null): string {
  if (durationMs === null) {
    return "unknown duration";
  }
  return `${(durationMs / 1000).toLocaleString("en-US", {
    maximumFractionDigits: 1,
  })} s`;
}

function ModelUsage({ attempt }: { attempt: AttemptSnapshot }) {
  if (attempt.model_provider !== "ollama") {
    return null;
  }
  const requestCount = formatCount(attempt.model_request_count);
  const requestLabel = attempt.model_request_count === 1 ? "request" : "requests";
  const inputLabel = `${formatCount(attempt.model_input_tokens)}${
    attempt.input_tokens_estimated ? " estimated" : ""
  } input`;
  const outputLabel = `${formatCount(attempt.model_output_tokens)} output tokens`;
  const providerCharge = attempt.provider_cost_estimate_usd === null
    ? "unknown"
    : `$${attempt.provider_cost_estimate_usd.toFixed(6)}`;

  return (
    <div
      className={`model-usage${attempt.usage_uncertain ? " usage-uncertain" : ""}`}
      data-testid="model-usage"
    >
      <p>
        <strong>{attempt.model_name ?? "Unknown local model"}</strong>
        {` · ${requestCount} ${requestLabel} · ${inputLabel} / ${outputLabel} · ${formatDuration(attempt.model_duration_ms)}`}
      </p>
      <p>
        Provider charge estimate: {providerCharge}
        <span> (excludes hardware and electricity)</span>
      </p>
      {attempt.usage_uncertain ? (
        <p className="usage-notice">
          Usage may be incomplete after an interrupted model request
        </p>
      ) : null}
    </div>
  );
}

export function RunTimeline({ steps }: { steps: StepSnapshot[] }) {
  const orderedSteps = [...steps].sort((left, right) => left.ordinal - right.ordinal);

  return (
    <section className="timeline-panel" aria-labelledby="timeline-title">
      <div className="section-heading">
        <p className="eyebrow">Durable progress</p>
        <h3 id="timeline-title">Run timeline</h3>
      </div>
      {orderedSteps.length === 0 ? (
        <p className="timeline-empty">Waiting for the first projected step.</p>
      ) : (
        <ol className="timeline-list">
          {orderedSteps.map((step) => {
            const attempts = [...step.attempts].sort(
              (left, right) => left.attempt_number - right.attempt_number,
            );
            return (
              <li className="timeline-step" key={`${step.ordinal}-${step.name}`}>
                <span className="timeline-marker" data-state={step.state}>
                  <StepIcon state={step.state} />
                </span>
                <div className="timeline-content">
                  <div className="timeline-step-title">
                    <h4>{formatStepLabel(step.name)}</h4>
                    <span>{formatState(step.state)}</span>
                  </div>
                  <p className="timeline-timestamp">
                    Updated <time dateTime={step.updated_at}>{formatUpdatedAt(step.updated_at)}</time>
                  </p>
                  <details
                    open={
                      attempts.length > 1 ||
                      attempts.some((attempt) => attempt.model_provider === "ollama")
                    }
                  >
                    <summary>
                      {attempts.length} {attempts.length === 1 ? "attempt" : "attempts"}
                    </summary>
                    <ol className="attempt-list">
                      {attempts.map((attempt) => (
                        <li key={attempt.attempt_number}>
                          <span>Attempt {attempt.attempt_number}</span>
                          <strong>{formatState(attempt.state)}</strong>
                          {attempt.failure_kind ? (
                            <em>{formatState(attempt.failure_kind)}</em>
                          ) : null}
                          <time dateTime={attempt.updated_at}>
                            {formatUpdatedAt(attempt.updated_at)}
                          </time>
                          <ModelUsage attempt={attempt} />
                        </li>
                      ))}
                    </ol>
                  </details>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
