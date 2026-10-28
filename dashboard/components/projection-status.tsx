import { ExternalLink, Radio } from "lucide-react";

import type { RunSnapshot, RunState } from "../lib/contracts";

const TERMINAL_STATES = new Set<RunState>([
  "SUCCEEDED",
  "FAILED",
  "DENIED",
  "CANCELLED",
  "TIMED_OUT",
  "BUDGET_EXHAUSTED",
  "NEEDS_ATTENTION",
]);

type ProjectionStatusProps = {
  observedAt: number;
  run: RunSnapshot;
};

export function ProjectionStatus({ observedAt, run }: ProjectionStatusProps) {
  const updatedAt = new Date(run.updated_at).valueOf();
  const delayed =
    !TERMINAL_STATES.has(run.state) &&
    Number.isFinite(updatedAt) &&
    observedAt - updatedAt > 10_000;

  return (
    <section className="projection-status" aria-labelledby="projection-title">
      <div className="projection-copy">
        <span className="projection-icon" aria-hidden="true">
          <Radio size={18} />
        </span>
        <div>
          <h3 id="projection-title">Application projection</h3>
          <p>{delayed ? "Projection may be delayed" : "Projection is current"}</p>
        </div>
      </div>
      <a href="http://127.0.0.1:8080" target="_blank" rel="noreferrer">
        Open Temporal UI
        <ExternalLink size={15} aria-hidden="true" />
      </a>
    </section>
  );
}
