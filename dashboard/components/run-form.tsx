import { FileText, LoaderCircle, RefreshCw, X } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { ApiError, listModelProfiles, submitRun } from "../lib/api";
import type { ModelProfileId, ModelProfileDescriptor, RunSnapshot } from "../lib/contracts";

const DEFAULT_GOAL = "Find recurring support themes";
const TICKET_FILES = ["ticket-001.txt", "ticket-002.txt"];

type RunFormProps = {
  submissionKey: string;
  onCancel: () => void;
  onSubmitted: (run: RunSnapshot) => Promise<void>;
};

export function RunForm({ submissionKey, onCancel, onSubmitted }: RunFormProps) {
  const [goal, setGoal] = useState(DEFAULT_GOAL);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ModelProfileDescriptor[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<ModelProfileId>("mock-v1");
  const [profilesPending, setProfilesPending] = useState(true);
  const [profilesError, setProfilesError] = useState<string | null>(null);
  const profileRequest = useRef<{
    controller: AbortController;
    token: symbol;
  } | null>(null);

  const loadProfiles = useCallback(async () => {
    if (profileRequest.current && !profileRequest.current.controller.signal.aborted) {
      return;
    }
    const controller = new AbortController();
    const token = Symbol("model-profile-request");
    profileRequest.current = { controller, token };
    setProfilesPending(true);
    setProfilesError(null);
    try {
      const available = await listModelProfiles(controller.signal);
      setProfiles(available);
      setSelectedProfileId((current) =>
        available.some((profile) => profile.profile_id === current)
          ? current
          : "mock-v1",
      );
    } catch (caught) {
      if (controller.signal.aborted) {
        return;
      }
      setProfiles([]);
      setSelectedProfileId("mock-v1");
      setProfilesError(
        caught instanceof ApiError
          ? caught.detail
          : "Could not load model profiles. Try again.",
      );
    } finally {
      if (profileRequest.current?.token === token) {
        profileRequest.current = null;
      }
      if (!controller.signal.aborted) {
        setProfilesPending(false);
      }
    }
  }, []);

  useEffect(() => {
    const startRequest = window.setTimeout(() => void loadProfiles(), 0);
    return () => {
      window.clearTimeout(startRequest);
      profileRequest.current?.controller.abort();
    };
  }, [loadProfiles]);

  const selectedProfile = profiles.find(
    (profile) => profile.profile_id === selectedProfileId,
  );

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || profilesPending || profilesError || !selectedProfile) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      const run = await submitRun({
        submission_key: submissionKey,
        goal,
        ticket_files: TICKET_FILES,
        model_profile_id: selectedProfile.profile_id,
      });
      await onSubmitted(run);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(caught.detail);
      } else {
        setError("Could not reach AgentForge. Try again.");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="new-run-panel" aria-labelledby="new-run-title">
      <header className="workspace-header">
        <div>
          <p className="eyebrow">Guided run workspace</p>
          <h2 id="new-run-title">Start a support report</h2>
          <p>AgentForge will analyze the approved local ticket snapshots.</p>
        </div>
        <button
          className="icon-button"
          type="button"
          aria-label="Close new run form"
          onClick={onCancel}
          disabled={pending}
        >
          <X size={20} aria-hidden="true" />
        </button>
      </header>

      <form className="run-form" onSubmit={handleSubmit}>
        <label htmlFor="run-goal">Goal</label>
        <textarea
          id="run-goal"
          name="goal"
          rows={4}
          maxLength={4096}
          required
          value={goal}
          onChange={(event) => setGoal(event.target.value)}
          disabled={pending}
        />

        <div className="model-profile-field">
          <div className="model-profile-label-row">
            <label htmlFor="model-profile">Model profile</label>
            <button
              className="text-button"
              type="button"
              aria-label="Refresh model profiles"
              onClick={() => void loadProfiles()}
              disabled={pending || profilesPending}
            >
              <RefreshCw size={14} aria-hidden="true" />
              Refresh
            </button>
          </div>
          <select
            id="model-profile"
            name="model_profile_id"
            value={selectedProfileId}
            onChange={(event) => setSelectedProfileId(event.target.value as ModelProfileId)}
            disabled={pending || profilesPending || Boolean(profilesError)}
          >
            {profilesPending && profiles.length === 0 ? (
              <option value="mock-v1">Loading profiles…</option>
            ) : null}
            {profiles.map((profile) => (
              <option key={profile.profile_id} value={profile.profile_id}>
                {profile.display_name}
              </option>
            ))}
          </select>
          {selectedProfile ? (
            <div className="model-profile-summary" aria-live="polite">
              <span>Provider: {selectedProfile.provider === "ollama" ? "Ollama" : "Mock"}</span>
              <span>Model: {selectedProfile.model_name}</span>
            </div>
          ) : null}
        </div>

        <fieldset>
          <legend>Approved ticket files</legend>
          <p>These local snapshots are fixed for this milestone.</p>
          <ul className="ticket-file-list">
            {TICKET_FILES.map((file) => (
              <li key={file}>
                <FileText size={17} aria-hidden="true" />
                {file}
              </li>
            ))}
          </ul>
        </fieldset>

        {profilesError ? <p role="alert" className="form-error">{profilesError}</p> : null}
        {error ? <p role="alert" className="form-error">{error}</p> : null}

        <div className="form-actions">
          <button className="secondary-button" type="button" onClick={onCancel} disabled={pending}>
            Cancel
          </button>
          <button
            className="primary-button"
            type="submit"
            disabled={pending || profilesPending || Boolean(profilesError) || !selectedProfile}
          >
            {pending ? <LoaderCircle className="spin" size={18} aria-hidden="true" /> : null}
            {pending ? "Starting…" : profilesPending ? "Loading profiles…" : "Start run"}
          </button>
        </div>
      </form>
    </section>
  );
}
