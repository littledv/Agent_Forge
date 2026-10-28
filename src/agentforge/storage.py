"""Safe local ticket reads and atomic artifact publication."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from uuid import UUID, uuid4

from agentforge.domain import ArtifactRef, DraftRef, FileReference, TicketSnapshotRef
from agentforge.multi_agent import (
    ResearchNoteRef,
    ReviewFeedbackRef,
    SUPPORT_REPORT_GRAPH_V2,
)


class StorageViolation(ValueError):
    """A requested file operation crossed an AgentForge storage boundary."""


class ArtifactStorage:
    SUPPORTED_TICKET_EXTENSIONS = {".md", ".txt"}

    def __init__(
        self,
        *,
        ticket_root: Path,
        artifact_root: Path,
        max_ticket_bytes: int,
        max_total_ticket_bytes: int,
    ) -> None:
        if max_ticket_bytes <= 0 or max_total_ticket_bytes <= 0:
            raise ValueError("ticket limits must be positive")
        if ticket_root.is_symlink():
            raise StorageViolation("ticket root must not be a symlink")
        self.ticket_root = ticket_root.resolve(strict=True)
        self.artifact_root = artifact_root.resolve()
        self.max_ticket_bytes = max_ticket_bytes
        self.max_total_ticket_bytes = max_total_ticket_bytes

    def snapshot_tickets(
        self, run_id: UUID, ticket_files: tuple[str, ...]
    ) -> TicketSnapshotRef:
        if not ticket_files:
            raise StorageViolation("at least one ticket is required")

        tickets: list[dict[str, str]] = []
        total_size = 0
        for relative_name in sorted(ticket_files):
            source = self._approved_ticket(relative_name)
            content = source.read_bytes()
            if len(content) > self.max_ticket_bytes:
                raise StorageViolation("ticket exceeds the individual size limit")
            total_size += len(content)
            if total_size > self.max_total_ticket_bytes:
                raise StorageViolation("tickets exceed the aggregate size limit")
            try:
                decoded = content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise StorageViolation("tickets must be valid UTF-8") from error
            tickets.append({"path": relative_name, "content": decoded})

        snapshot_bytes = (
            json.dumps(
                {"tickets": tickets},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        relative_path = f"snapshots/{run_id}/tickets.json"
        self._atomic_write(run_id, relative_path, snapshot_bytes)
        return TicketSnapshotRef(
            path=relative_path,
            sha256=hashlib.sha256(snapshot_bytes).hexdigest(),
            size_bytes=len(snapshot_bytes),
            ticket_count=len(tickets),
        )

    def read_ticket_snapshot(
        self, reference: TicketSnapshotRef
    ) -> list[dict[str, str]]:
        content = self._verified_artifact_bytes(reference.path, reference.sha256)
        if len(content) != reference.size_bytes:
            raise StorageViolation("snapshot size does not match its reference")
        try:
            document: Any = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise StorageViolation("snapshot is not valid UTF-8 JSON") from error
        if not isinstance(document, dict) or set(document) != {"tickets"}:
            raise StorageViolation("snapshot has an invalid shape")
        tickets = document["tickets"]
        if not isinstance(tickets, list) or len(tickets) != reference.ticket_count:
            raise StorageViolation("snapshot ticket count does not match its reference")
        normalized: list[dict[str, str]] = []
        for ticket in tickets:
            if (
                not isinstance(ticket, dict)
                or set(ticket) != {"path", "content"}
                or not isinstance(ticket["path"], str)
                or not isinstance(ticket["content"], str)
            ):
                raise StorageViolation("snapshot contains an invalid ticket")
            normalized.append({"path": ticket["path"], "content": ticket["content"]})
        return normalized

    def write_draft(self, run_id: UUID, content: bytes) -> DraftRef:
        relative_path = f"drafts/{run_id}/support-report.md"
        self._atomic_write(run_id, relative_path, content)
        return DraftRef(
            path=relative_path,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )

    def write_research_note(
        self, run_id: UUID, shard_index: int, content: bytes, *, max_chars: int
    ) -> ResearchNoteRef:
        self._validate_stage_index(
            shard_index,
            maximum=SUPPORT_REPORT_GRAPH_V2.max_research_tasks - 1,
            label="research shard",
        )
        relative_path, digest = self._write_stage(
            run_id,
            directory=f"research/{run_id}/shard-{shard_index}",
            suffix=".json",
            content=content,
            max_chars=max_chars,
        )
        return ResearchNoteRef(
            path=relative_path, sha256=digest, size_bytes=len(content)
        )

    def read_research_note(self, run_id: UUID, ref: ResearchNoteRef) -> bytes:
        return self._read_stage(
            run_id,
            ref,
            root="research",
            index_label="shard",
            maximum_index=SUPPORT_REPORT_GRAPH_V2.max_research_tasks - 1,
            suffix=".json",
        )

    def write_revision_draft(
        self, run_id: UUID, revision: int, content: bytes, *, max_chars: int
    ) -> DraftRef:
        self._validate_stage_index(
            revision,
            maximum=SUPPORT_REPORT_GRAPH_V2.max_revisions,
            label="draft revision",
        )
        relative_path, digest = self._write_stage(
            run_id,
            directory=f"drafts/{run_id}/revision-{revision}",
            suffix=".md",
            content=content,
            max_chars=max_chars,
        )
        return DraftRef(path=relative_path, sha256=digest, size_bytes=len(content))

    def read_revision_draft(self, run_id: UUID, ref: DraftRef) -> bytes:
        return self._read_stage(
            run_id,
            ref,
            root="drafts",
            index_label="revision",
            maximum_index=SUPPORT_REPORT_GRAPH_V2.max_revisions,
            suffix=".md",
        )

    def write_review_feedback(
        self, run_id: UUID, round_number: int, content: bytes, *, max_chars: int
    ) -> ReviewFeedbackRef:
        self._validate_stage_index(
            round_number,
            maximum=SUPPORT_REPORT_GRAPH_V2.max_revisions,
            label="review round",
        )
        relative_path, digest = self._write_stage(
            run_id,
            directory=f"reviews/{run_id}/round-{round_number}",
            suffix=".json",
            content=content,
            max_chars=max_chars,
        )
        return ReviewFeedbackRef(
            path=relative_path, sha256=digest, size_bytes=len(content)
        )

    def read_review_feedback(
        self, run_id: UUID, ref: ReviewFeedbackRef
    ) -> bytes:
        return self._read_stage(
            run_id,
            ref,
            root="reviews",
            index_label="round",
            maximum_index=SUPPORT_REPORT_GRAPH_V2.max_revisions,
            suffix=".json",
        )

    def publish_reviewed_report(self, run_id: UUID, draft: DraftRef) -> ArtifactRef:
        content = self.read_revision_draft(run_id, draft)
        relative_path = f"reports/{run_id}/support-report.md"
        self._atomic_write(run_id, relative_path, content)
        return ArtifactRef(
            path=relative_path,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            media_type="text/markdown",
        )

    def publish_report(self, run_id: UUID, draft: DraftRef) -> ArtifactRef:
        expected_draft = f"drafts/{run_id}/support-report.md"
        if draft.path != expected_draft:
            raise StorageViolation("draft does not belong to this run")
        content = self._verified_artifact_bytes(draft.path, draft.sha256)
        if len(content) != draft.size_bytes:
            raise StorageViolation("draft size does not match its reference")

        relative_path = f"reports/{run_id}/support-report.md"
        self._atomic_write(run_id, relative_path, content)
        return ArtifactRef(
            path=relative_path,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            media_type="text/markdown",
        )

    def read_verified_artifact(self, reference: ArtifactRef) -> bytes:
        if reference.media_type != "text/markdown":
            raise StorageViolation("artifact media type is not approved for delivery")
        content = self._verified_artifact_bytes(reference.path, reference.sha256)
        if len(content) != reference.size_bytes:
            raise StorageViolation("artifact size does not match its reference")
        return content

    def _approved_ticket(self, relative_name: str) -> Path:
        path = PurePosixPath(relative_name)
        if (
            not relative_name
            or "\\" in relative_name
            or path.is_absolute()
            or PureWindowsPath(relative_name).is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise StorageViolation("ticket path must be a safe relative POSIX path")
        if path.suffix.lower() not in self.SUPPORTED_TICKET_EXTENSIONS:
            raise StorageViolation("ticket extension is not allowed")

        candidate = self.ticket_root.joinpath(*path.parts)
        current = self.ticket_root
        for part in path.parts:
            current = current / part
            if current.is_symlink():
                raise StorageViolation("ticket path must not contain a symlink")
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError) as error:
            raise StorageViolation("ticket does not exist") from error
        if not resolved.is_relative_to(self.ticket_root) or not resolved.is_file():
            raise StorageViolation("ticket must be a file inside the approved root")
        return resolved

    def _verified_artifact_bytes(self, relative_name: str, expected_hash: str) -> bytes:
        path = self.artifact_root.joinpath(*PurePosixPath(relative_name).parts)
        try:
            resolved = path.resolve(strict=True)
        except (FileNotFoundError, OSError) as error:
            raise StorageViolation("referenced artifact does not exist") from error
        if not resolved.is_relative_to(self.artifact_root) or not resolved.is_file():
            raise StorageViolation("artifact reference leaves the configured root")
        content = resolved.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise StorageViolation("artifact checksum does not match its reference")
        return content

    def _write_stage(
        self,
        run_id: UUID,
        *,
        directory: str,
        suffix: str,
        content: bytes,
        max_chars: int,
    ) -> tuple[str, str]:
        if isinstance(max_chars, bool) or max_chars <= 0:
            raise ValueError("max_chars must be positive")
        if not isinstance(content, bytes):
            raise TypeError("stage content must be bytes")
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise StorageViolation("stage content must be valid UTF-8") from error
        if len(decoded) > max_chars:
            raise StorageViolation("stage content exceeds its character limit")

        digest = hashlib.sha256(content).hexdigest()
        relative_path = f"{directory}/{digest}{suffix}"
        self._create_once(run_id, relative_path, content)
        return relative_path, digest

    def _read_stage(
        self,
        run_id: UUID,
        ref: FileReference,
        *,
        root: str,
        index_label: str,
        maximum_index: int,
        suffix: str,
    ) -> bytes:
        parts = PurePosixPath(ref.path).parts
        if len(parts) != 4 or parts[0] != root or parts[1] != str(run_id):
            raise StorageViolation("stage reference does not belong to this run or kind")
        prefix = f"{index_label}-"
        if not parts[2].startswith(prefix):
            raise StorageViolation("stage reference has an invalid index")
        index_text = parts[2][len(prefix) :]
        if not index_text.isdecimal():
            raise StorageViolation("stage reference has an invalid index")
        index = int(index_text)
        if parts[2] != f"{index_label}-{index}" or not 0 <= index <= maximum_index:
            raise StorageViolation("stage reference has an invalid index")
        if parts[3] != f"{ref.sha256}{suffix}":
            raise StorageViolation("stage path does not match its checksum")

        content = self._verified_artifact_bytes(ref.path, ref.sha256)
        if len(content) != ref.size_bytes:
            raise StorageViolation("stage size does not match its reference")
        return content

    @staticmethod
    def _validate_stage_index(value: int, *, maximum: int, label: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
            raise ValueError(f"{label} index must be between 0 and {maximum}")

    def _create_once(self, run_id: UUID, relative_name: str, content: bytes) -> None:
        destination = self.artifact_root.joinpath(*PurePosixPath(relative_name).parts)
        if not destination.resolve().is_relative_to(self.artifact_root):
            raise StorageViolation("artifact destination leaves the configured root")
        staging = self.artifact_root / ".staging" / str(run_id)
        staging.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = staging / f"{uuid4()}.tmp"
        try:
            with temporary.open("xb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.read_bytes() != content:
                    raise StorageViolation(
                        "immutable stage destination contains different content"
                    )
        finally:
            temporary.unlink(missing_ok=True)

    def _atomic_write(self, run_id: UUID, relative_name: str, content: bytes) -> None:
        destination = self.artifact_root.joinpath(*PurePosixPath(relative_name).parts)
        if not destination.resolve().is_relative_to(self.artifact_root):
            raise StorageViolation("artifact destination leaves the configured root")
        staging = self.artifact_root / ".staging" / str(run_id)
        staging.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = staging / f"{uuid4()}.tmp"
        try:
            with temporary.open("xb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
