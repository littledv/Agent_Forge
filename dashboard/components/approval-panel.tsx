import { Check, ShieldCheck, X } from "lucide-react";

import type { ApprovalDecision, ApprovalSnapshot } from "../lib/contracts";
import { formatState } from "../lib/format";

type ApprovalPanelProps = {
  approval: ApprovalSnapshot;
  mutationPending: boolean;
  onDecide: (decision: ApprovalDecision) => void;
};

export function ApprovalPanel({
  approval,
  mutationPending,
  onDecide,
}: ApprovalPanelProps) {
  return (
    <section className="context-panel approval-panel" aria-labelledby="approval-title">
      <div className="context-panel-title">
        <ShieldCheck size={18} aria-hidden="true" />
        <h3 id="approval-title">Delivery approval</h3>
      </div>
      <dl className="metadata-list">
        <div>
          <dt>State</dt>
          <dd>{formatState(approval.state)}</dd>
        </div>
        <div>
          <dt>Target</dt>
          <dd>{approval.action.target}</dd>
        </div>
        <div>
          <dt>Action hash</dt>
          <dd className="hash-value">{approval.action_hash}</dd>
        </div>
      </dl>
      {approval.state === "PENDING" ? (
        <div className="approval-actions">
          <button
            className="secondary-button reject-button"
            type="button"
            disabled={mutationPending}
            onClick={() => onDecide("REJECT")}
          >
            <X size={17} aria-hidden="true" />
            Reject
          </button>
          <button
            className="primary-button"
            type="button"
            disabled={mutationPending}
            onClick={() => onDecide("APPROVE")}
          >
            <Check size={17} aria-hidden="true" />
            Approve
          </button>
        </div>
      ) : null}
    </section>
  );
}
