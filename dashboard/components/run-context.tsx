import { Ban, Database, PackageCheck } from "lucide-react";
import { useState } from "react";

import type {
  ApprovalDecision,
  RunDetailSnapshot,
  RunState,
} from "../lib/contracts";
import { ApprovalPanel } from "./approval-panel";
import { ArtifactPanel } from "./artifact-panel";

const CANCELLATION_CLOSED_STATES = new Set<RunState>([
  "CANCELLATION_REQUESTED",
  "SUCCEEDED",
  "FAILED",
  "DENIED",
  "CANCELLED",
  "TIMED_OUT",
  "BUDGET_EXHAUSTED",
  "NEEDS_ATTENTION",
]);

type RunContextProps = {
  detail: RunDetailSnapshot;
  mutationPending: boolean;
  onCancel: () => void;
  onDecide: (decision: ApprovalDecision) => void;
};

export function RunContext({
  detail,
  mutationPending,
  onCancel,
  onDecide,
}: RunContextProps) {
  const [confirmingCancellation, setConfirmingCancellation] = useState(false);
  const artifact = detail.approval?.action.artifact ?? detail.run.artifact;
  const cancellationRequested =
    detail.cancellation_requested || detail.run.state === "CANCELLATION_REQUESTED";
  const canCancel =
    !cancellationRequested &&
    !CANCELLATION_CLOSED_STATES.has(detail.run.state);
  const artifactKey = artifact
    ? [
        detail.run.run_id,
        artifact.path,
        artifact.media_type,
        artifact.size_bytes,
        artifact.sha256,
      ].join(":")
    : null;
  const modelDigest = detail.model_profile.model_digest;

  return (
    <div className="run-context-content">
      {artifact ? (
        <ArtifactPanel key={artifactKey} artifact={artifact} runId={detail.run.run_id} />
      ) : null}
      {detail.approval ? (
        <ApprovalPanel
          approval={detail.approval}
          mutationPending={mutationPending}
          onDecide={onDecide}
        />
      ) : null}

      {detail.delivery ? (
        <section className="context-panel" aria-labelledby="delivery-title">
          <div className="context-panel-title">
            <PackageCheck size={18} aria-hidden="true" />
            <h3 id="delivery-title">Delivery receipt</h3>
          </div>
          <dl className="metadata-list">
            <div>
              <dt>Delivery ID</dt>
              <dd>{detail.delivery.delivery_id}</dd>
            </div>
            <div>
              <dt>Operation ID</dt>
              <dd>{detail.delivery.operation_id}</dd>
            </div>
            <div>
              <dt>Committed</dt>
              <dd>
                <time dateTime={detail.delivery.committed_at}>
                  {detail.delivery.committed_at}
                </time>
              </dd>
            </div>
            <div>
              <dt>Artifact SHA-256</dt>
              <dd className="hash-value">{detail.delivery.artifact_sha256}</dd>
            </div>
          </dl>
        </section>
      ) : null}

      <section className="context-panel" aria-labelledby="model-profile-title">
        <div className="context-panel-title">
          <Database size={18} aria-hidden="true" />
          <h3 id="model-profile-title">Model profile</h3>
        </div>
        <dl className="metadata-list">
          <div>
            <dt>Profile ID</dt>
            <dd>{detail.model_profile.profile_id}</dd>
          </div>
          <div>
            <dt>Provider</dt>
            <dd>{detail.model_profile.provider === "ollama" ? "Ollama" : "Mock"}</dd>
          </div>
          <div>
            <dt>Model</dt>
            <dd>{detail.model_profile.model_name}</dd>
          </div>
          <div>
            <dt>Digest</dt>
            <dd className="hash-value">
              {modelDigest ? `${modelDigest.slice(0, 12)}…` : "Not applicable"}
            </dd>
          </div>
        </dl>
      </section>

      <section className="context-panel" aria-labelledby="execution-title">
        <div className="context-panel-title">
          <Database size={18} aria-hidden="true" />
          <h3 id="execution-title">Execution reference</h3>
        </div>
        <dl className="metadata-list">
          <div>
            <dt>Workflow</dt>
            <dd>{detail.run.workflow_id}</dd>
          </div>
          <div>
            <dt>Tickets</dt>
            <dd>{detail.ticket_files.length}</dd>
          </div>
          <div>
            <dt>Projection</dt>
            <dd>#{detail.projection_sequence}</dd>
          </div>
        </dl>
      </section>

      {canCancel ? (
        <section className="context-panel cancellation-panel" aria-labelledby="cancel-title">
          <div className="context-panel-title">
            <Ban size={18} aria-hidden="true" />
            <h3 id="cancel-title">Cancel run</h3>
          </div>
          {confirmingCancellation ? (
            <div className="cancel-confirmation">
              <strong>Cancel {detail.goal}?</strong>
              <span className="selected-run-id">{detail.run.run_id}</span>
              <p>Committed delivery cannot be undone.</p>
              <span>Cancel only stops work that durable execution can still stop.</span>
              <div className="confirmation-actions">
                <button
                  className="secondary-button"
                  type="button"
                  disabled={mutationPending}
                  onClick={() => setConfirmingCancellation(false)}
                >
                  Keep run
                </button>
                <button
                  className="danger-button"
                  type="button"
                  disabled={mutationPending}
                  onClick={onCancel}
                >
                  Confirm cancellation
                </button>
              </div>
            </div>
          ) : (
            <button
              className="secondary-button cancel-run-button"
              type="button"
              disabled={mutationPending}
              onClick={() => setConfirmingCancellation(true)}
            >
              Cancel run
            </button>
          )}
        </section>
      ) : null}
      {cancellationRequested ? (
        <section className="context-panel cancellation-panel" aria-labelledby="cancel-title">
          <div className="context-panel-title">
            <Ban size={18} aria-hidden="true" />
            <h3 id="cancel-title">Cancellation requested</h3>
          </div>
          <p className="context-panel-copy">
            Durable execution has recorded the request. The projected run state will
            change when the Workflow reaches a safe cancellation point.
          </p>
        </section>
      ) : null}
    </div>
  );
}
