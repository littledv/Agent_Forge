import { FileText } from "lucide-react";
import { type SyntheticEvent, useState } from "react";

import { ApiError, getArtifact } from "../lib/api";
import type { ArtifactRef } from "../lib/contracts";

type ArtifactPanelProps = {
  artifact: ArtifactRef;
  runId: string;
};

export function ArtifactPanel({ artifact, runId }: ArtifactPanelProps) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<{
    message: string;
    retryable: boolean;
  } | null>(null);

  async function loadContent() {
    if (content !== null || loading) {
      return;
    }
    setLoading(true);
    setFailure(null);
    try {
      const response = await getArtifact(runId);
      if (
        response.artifact.sha256 !== artifact.sha256 ||
        response.artifact.path !== artifact.path ||
        response.artifact.size_bytes !== artifact.size_bytes ||
        response.artifact.media_type !== artifact.media_type
      ) {
        setFailure({ message: "Artifact could not be verified", retryable: false });
        return;
      }
      setContent(response.content);
    } catch (caught) {
      const verificationFailure = caught instanceof ApiError && caught.status === 409;
      setFailure({
        message: verificationFailure
          ? "Artifact could not be verified"
          : "Report content is temporarily unavailable",
        retryable: !verificationFailure,
      });
    } finally {
      setLoading(false);
    }
  }

  async function handleToggle(event: SyntheticEvent<HTMLDetailsElement>) {
    if (!event.currentTarget.open || failure !== null) {
      return;
    }
    await loadContent();
  }

  return (
    <section className="context-panel artifact-panel" aria-labelledby="artifact-title">
      <div className="context-panel-title">
        <FileText size={18} aria-hidden="true" />
        <h3 id="artifact-title">Report artifact</h3>
      </div>
      <dl className="metadata-list">
        <div>
          <dt>Path</dt>
          <dd>{artifact.path}</dd>
        </div>
        <div>
          <dt>Media type</dt>
          <dd>{artifact.media_type}</dd>
        </div>
        <div>
          <dt>Size</dt>
          <dd>{artifact.size_bytes} bytes</dd>
        </div>
        <div>
          <dt>SHA-256</dt>
          <dd className="hash-value">{artifact.sha256}</dd>
        </div>
      </dl>
      <details className="artifact-disclosure" onToggle={(event) => void handleToggle(event)}>
        <summary>Report content</summary>
        {loading ? <p className="artifact-message">Verifying report…</p> : null}
        {failure ? <p className="artifact-error">{failure.message}</p> : null}
        {failure?.retryable ? (
          <button
            className="secondary-button artifact-retry-button"
            type="button"
            disabled={loading}
            onClick={() => void loadContent()}
          >
            Retry report content
          </button>
        ) : null}
        {content !== null ? <pre className="artifact-content">{content}</pre> : null}
      </details>
    </section>
  );
}
