import { Clock3, Workflow } from "lucide-react";

import type { RunListItem } from "../lib/contracts";
import { formatState, formatUpdatedAt } from "../lib/format";

type RunListProps = {
  runs: RunListItem[];
  selectedRunId: string | null;
  loading: boolean;
  onSelect: (runId: string) => void;
};

export function RunList({ runs, selectedRunId, loading, onSelect }: RunListProps) {
  if (loading && runs.length === 0) {
    return <p className="rail-message">Loading recent runs…</p>;
  }

  if (runs.length === 0) {
    return (
      <div className="rail-empty">
        <Workflow size={18} aria-hidden="true" />
        <p>No recent runs yet</p>
      </div>
    );
  }

  return (
    <div className="run-list">
      {runs.map((run) => (
        <button
          className="run-list-item"
          data-selected={selectedRunId === run.run_id}
          key={run.run_id}
          type="button"
          aria-pressed={selectedRunId === run.run_id}
          onClick={() => onSelect(run.run_id)}
        >
          <span className="run-list-goal">{run.goal}</span>
          <span className="run-list-meta">
            <span className="run-state">{formatState(run.state)}</span>
            <span>
              <Clock3 size={13} aria-hidden="true" />
              {formatUpdatedAt(run.updated_at)}
            </span>
          </span>
        </button>
      ))}
    </div>
  );
}
