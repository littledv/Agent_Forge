"use client";

import { FileText, Orbit, Plus } from "lucide-react";
import { useCallback, useState } from "react";

import { ApiError, cancelRun, decideApproval } from "../lib/api";
import type { ApprovalDecision, RunSnapshot } from "../lib/contracts";
import { useRunPolling } from "../lib/polling";
import { ProjectionStatus } from "./projection-status";
import { RunForm } from "./run-form";
import { RunHeader } from "./run-header";
import { RunContext } from "./run-context";
import { RunList } from "./run-list";
import { RunTimeline } from "./run-timeline";

export function RunDashboard() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [submissionKey, setSubmissionKey] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [mutationPending, setMutationPending] = useState(false);
  const [refreshPending, setRefreshPending] = useState(false);
  const [projectionUncertain, setProjectionUncertain] = useState(false);
  const handleSelectedRunMissing = useCallback(() => {
    setSelectedRunId(null);
    setNotice("That run is no longer available");
  }, []);
  const {
    runs,
    detail,
    detailObservedAt,
    listLoading,
    detailLoading,
    serviceNotice,
    refreshRuns,
    refreshDetail,
  } = useRunPolling(selectedRunId, {
    onSelectedRunMissing: handleSelectedRunMissing,
  });

  function selectRun(runId: string) {
    setSubmissionKey(null);
    setSelectedRunId(runId);
    setNotice(null);
    setProjectionUncertain(false);
  }

  function openForm() {
    setSelectedRunId(null);
    setNotice(null);
    setProjectionUncertain(false);
    setSubmissionKey(crypto.randomUUID());
  }

  function closeForm() {
    setSubmissionKey(null);
  }

  async function handleSubmitted(run: RunSnapshot) {
    setSubmissionKey(null);
    setSelectedRunId(run.run_id);
    setNotice(null);
    setProjectionUncertain(false);
    refreshRuns();
  }

  async function refreshAfterMutation(conflict: boolean) {
    try {
      await refreshDetail();
      setProjectionUncertain(false);
      if (conflict) {
        setNotice("Durable state changed; refreshed the winning decision");
      }
    } catch {
      setProjectionUncertain(true);
      setNotice(
        conflict
          ? "Durable state changed, but the winning projection could not be refreshed. Refresh the run before taking another action."
          : "The action was recorded, but the current projection could not be refreshed. Refresh the run before taking another action.",
      );
    }
  }

  async function handleManualRefresh() {
    if (!selectedRunId || refreshPending || mutationPending) {
      return;
    }
    setRefreshPending(true);
    setNotice(null);
    try {
      await refreshDetail();
      setProjectionUncertain(false);
    } catch (caught) {
      setNotice(
        caught instanceof ApiError ? caught.detail : "Could not refresh current projection",
      );
    } finally {
      setRefreshPending(false);
    }
  }

  async function handleDecision(decision: ApprovalDecision) {
    if (!detail?.approval || mutationPending) {
      return;
    }
    const { approval_id: approvalId, action_hash: actionHash } = detail.approval;
    await executeMutation(
      () => decideApproval(approvalId, actionHash, decision),
      "Could not update approval",
    );
  }

  async function handleCancel(runId: string) {
    if (mutationPending) {
      return;
    }
    await executeMutation(() => cancelRun(runId), "Could not cancel run");
  }

  async function executeMutation(
    request: () => Promise<unknown>,
    fallbackMessage: string,
  ) {
    setMutationPending(true);
    setNotice(null);
    try {
      try {
        await request();
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 409) {
          await refreshAfterMutation(true);
        } else {
          setNotice(caught instanceof ApiError ? caught.detail : fallbackMessage);
        }
        return;
      }
      await refreshAfterMutation(false);
    } finally {
      setMutationPending(false);
    }
  }

  const controlsDisabled = mutationPending || refreshPending || projectionUncertain;

  return (
    <div className="workspace-shell">
      <aside className="run-rail" aria-label="Run navigation">
        <div>
          <header className="brand">
            <span className="brand-mark" aria-hidden="true">
              <Orbit size={22} strokeWidth={2.4} />
            </span>
            <h1>AgentForge</h1>
          </header>

          <button className="new-run-button" type="button" onClick={openForm}>
            <Plus size={19} aria-hidden="true" />
            New run
          </button>

          <section className="recent-runs" aria-labelledby="recent-runs-title">
            <h2 id="recent-runs-title">Recent runs</h2>
            <RunList
              runs={runs}
              selectedRunId={selectedRunId}
              loading={listLoading}
              onSelect={selectRun}
            />
          </section>
        </div>

      </aside>

      <main className="run-workspace">
        {notice ? <p className="workspace-notice" role="status">{notice}</p> : null}
        {serviceNotice ? (
          <p className="service-notice" role="status">{serviceNotice}</p>
        ) : null}
        {submissionKey ? (
          <RunForm
            key={submissionKey}
            submissionKey={submissionKey}
            onCancel={closeForm}
            onSubmitted={handleSubmitted}
          />
        ) : detail ? (
          <section className="selected-run" aria-labelledby="selected-run-title">
            <RunHeader
              goal={detail.goal}
              run={detail.run}
              refreshPending={refreshPending || mutationPending}
              onRefresh={() => void handleManualRefresh()}
            />
            <ProjectionStatus observedAt={detailObservedAt} run={detail.run} />
            <RunTimeline steps={detail.steps} />
          </section>
        ) : detailLoading && selectedRunId ? (
          <div className="empty-workspace">
            <p className="eyebrow">Guided run workspace</p>
            <h2>Loading run…</h2>
          </div>
        ) : (
          <div className="empty-workspace">
            <span className="empty-icon" aria-hidden="true">
              <FileText size={27} strokeWidth={1.8} />
            </span>
            <p className="eyebrow">Guided run workspace</p>
            <h2>No run selected</h2>
            <p>Select a recent run or start a new support report.</p>
          </div>
        )}
      </main>

      <aside className="context-rail" aria-labelledby="run-context-title">
        <header>
          <p className="eyebrow">Run details</p>
          <h2 id="run-context-title">Run context</h2>
        </header>
        {detail ? (
          <RunContext
            key={detail.run.run_id}
            detail={detail}
            mutationPending={controlsDisabled}
            onCancel={() => void handleCancel(detail.run.run_id)}
            onDecide={(decision) => void handleDecision(decision)}
          />
        ) : (
          <div className="context-empty">
            <p>Nothing to inspect yet.</p>
            <span>Choose a run to review its projection, approvals, and report.</span>
          </div>
        )}
      </aside>
    </div>
  );
}
