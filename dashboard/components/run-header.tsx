import {
  Ban,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  Clock3,
  Hand,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";

import type { RunSnapshot } from "../lib/contracts";
import { formatState, formatUpdatedAt } from "../lib/format";

function StateIcon({ state }: { state: RunSnapshot["state"] }) {
  if (state === "SUCCEEDED") {
    return <CircleCheck size={18} aria-hidden="true" />;
  }
  if (state === "RUNNING" || state === "PENDING_START") {
    return <LoaderCircle size={18} aria-hidden="true" />;
  }
  if (state === "WAITING_APPROVAL") {
    return <Hand size={18} aria-hidden="true" />;
  }
  if (state === "CANCELLED" || state === "CANCELLATION_REQUESTED") {
    return <Ban size={18} aria-hidden="true" />;
  }
  if (state === "FAILED" || state === "DENIED") {
    return <CircleAlert size={18} aria-hidden="true" />;
  }
  return <CircleDashed size={18} aria-hidden="true" />;
}

type RunHeaderProps = {
  goal: string;
  run: RunSnapshot;
  refreshPending: boolean;
  onRefresh: () => void;
};

export function RunHeader({ goal, run, refreshPending, onRefresh }: RunHeaderProps) {
  return (
    <header className="selected-run-header">
      <p className="eyebrow">Guided run workspace</p>
      <div className="run-title-line">
        <h2 id="selected-run-title">{goal}</h2>
        <span className="state-badge" data-state={run.state}>
          <StateIcon state={run.state} />
          {formatState(run.state)}
        </span>
      </div>
      <p className="selected-run-id">{run.run_id}</p>
      <div className="run-update-line">
        <Clock3 size={15} aria-hidden="true" />
        <span>Application projection updated {formatUpdatedAt(run.updated_at)}</span>
        <button
          className="refresh-run-button"
          type="button"
          disabled={refreshPending}
          onClick={onRefresh}
        >
          <RefreshCw size={14} aria-hidden="true" />
          Refresh run
        </button>
      </div>
      {run.failure_kind ? (
        <p className="failure-kind">Failure kind: {formatState(run.failure_kind)}</p>
      ) : null}
      {run.state === "NEEDS_ATTENTION" ? (
        <p className="needs-attention-guidance">
          An external result could not be reconciled. Review Temporal history and the
          delivery receipt before deciding whether operator action is required.
        </p>
      ) : null}
    </header>
  );
}
