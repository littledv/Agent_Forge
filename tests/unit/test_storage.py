from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import UUID

import pytest

from agentforge.domain import DraftRef
from agentforge.multi_agent import ResearchNoteRef, ReviewFeedbackRef
from agentforge.storage import ArtifactStorage, StorageViolation


RUN_ID = UUID("00000000-0000-0000-0000-000000000001")


def storage(ticket_root: Path, artifact_root: Path) -> ArtifactStorage:
    return ArtifactStorage(
        ticket_root=ticket_root,
        artifact_root=artifact_root,
        max_ticket_bytes=65_536,
        max_total_ticket_bytes=262_144,
    )


def test_ticket_snapshot_is_deterministic_and_lexically_ordered(tmp_path: Path) -> None:
    tickets = tmp_path / "tickets"
    artifacts = tmp_path / "artifacts"
    tickets.mkdir()
    (tickets / "b.md").write_text("Second ticket\n", encoding="utf-8")
    (tickets / "a.txt").write_text("First ticket\n", encoding="utf-8")

    result = storage(tickets, artifacts).snapshot_tickets(
        RUN_ID, ("b.md", "a.txt")
    )
    snapshot = (artifacts / result.path).read_bytes()

    assert result.path == f"snapshots/{RUN_ID}/tickets.json"
    assert result.ticket_count == 2
    assert result.size_bytes == len(snapshot)
    assert result.sha256 == hashlib.sha256(snapshot).hexdigest()
    assert snapshot.index(b'a.txt') < snapshot.index(b'b.md')


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "C:/private/ticket.txt",
        "/private/ticket.txt",
        "missing.txt",
        "folder",
        "ticket.exe",
    ],
)
def test_ticket_snapshot_rejects_disallowed_sources(tmp_path: Path, name: str) -> None:
    tickets = tmp_path / "tickets"
    artifacts = tmp_path / "artifacts"
    tickets.mkdir()
    (tickets / "folder").mkdir()
    (tickets / "ticket.exe").write_text("no", encoding="utf-8")

    with pytest.raises(StorageViolation):
        storage(tickets, artifacts).snapshot_tickets(RUN_ID, (name,))
    assert not artifacts.exists()


def test_ticket_snapshot_rejects_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tickets = tmp_path / "tickets"
    artifacts = tmp_path / "artifacts"
    tickets.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    link = tickets / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        link.write_text("simulated link", encoding="utf-8")
        original_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda path: path == link or original_is_symlink(path),
        )

    with pytest.raises(StorageViolation):
        storage(tickets, artifacts).snapshot_tickets(RUN_ID, ("linked.txt",))


@pytest.mark.parametrize(
    ("sizes", "selected"),
    [
        ([65_537], ("one.txt",)),
        ([65_536, 65_536, 65_536, 65_537], ("one.txt", "two.txt", "three.txt", "four.txt")),
    ],
)
def test_ticket_snapshot_enforces_size_limits_without_partial_output(
    tmp_path: Path, sizes: list[int], selected: tuple[str, ...]
) -> None:
    tickets = tmp_path / "tickets"
    artifacts = tmp_path / "artifacts"
    tickets.mkdir()
    for name, size in zip(("one.txt", "two.txt", "three.txt", "four.txt"), sizes):
        (tickets / name).write_bytes(b"x" * size)

    with pytest.raises(StorageViolation):
        storage(tickets, artifacts).snapshot_tickets(RUN_ID, selected)
    assert not (artifacts / "snapshots").exists()


def test_report_publication_is_atomic_and_verifiable(tmp_path: Path) -> None:
    tickets = tmp_path / "tickets"
    artifacts = tmp_path / "artifacts"
    tickets.mkdir()
    store = storage(tickets, artifacts)
    draft = store.write_draft(RUN_ID, b"# Deterministic report\n")

    result = store.publish_report(RUN_ID, draft)
    report = artifacts / result.path

    assert result.path == f"reports/{RUN_ID}/support-report.md"
    assert result.media_type == "text/markdown"
    assert result.size_bytes == report.stat().st_size
    assert result.sha256 == hashlib.sha256(report.read_bytes()).hexdigest()
    assert not any((artifacts / ".staging" / str(RUN_ID)).glob("*"))


def test_failed_atomic_replacement_leaves_no_partial_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tickets = tmp_path / "tickets"
    artifacts = tmp_path / "artifacts"
    tickets.mkdir()
    store = storage(tickets, artifacts)
    draft = store.write_draft(RUN_ID, b"# Draft\n")

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        raise OSError("forced replacement failure")

    monkeypatch.setattr("agentforge.storage.os.replace", fail_replace)
    with pytest.raises(OSError, match="forced replacement failure"):
        store.publish_report(RUN_ID, draft)

    assert not (artifacts / f"reports/{RUN_ID}/support-report.md").exists()
    assert not any((artifacts / ".staging" / str(RUN_ID)).glob("*"))


def test_read_verified_artifact_requires_exact_metadata(tmp_path: Path) -> None:
    tickets = tmp_path / "tickets"
    artifacts = tmp_path / "artifacts"
    tickets.mkdir()
    store = storage(tickets, artifacts)
    draft = store.write_draft(RUN_ID, b"# Approved report\n")
    artifact = store.publish_report(RUN_ID, draft)

    assert store.read_verified_artifact(artifact) == b"# Approved report\n"

    (artifacts / artifact.path).write_bytes(b"# Changed report\n")
    with pytest.raises(StorageViolation):
        store.read_verified_artifact(artifact)


def test_role_stages_use_content_hash_paths_and_round_trip_exact_bytes(
    tmp_path: Path,
) -> None:
    tickets = tmp_path / "tickets"
    artifacts = tmp_path / "artifacts"
    tickets.mkdir()
    store = storage(tickets, artifacts)

    note_bytes = '{"findings":[]}\n'.encode()
    draft_bytes = "# Résumé\n".encode()
    feedback_bytes = '{"items":[{"code":"FORMAT","text":"Add headings"}]}\n'.encode()
    note = store.write_research_note(RUN_ID, 2, note_bytes, max_chars=4_096)
    draft = store.write_revision_draft(RUN_ID, 1, draft_bytes, max_chars=16_384)
    feedback = store.write_review_feedback(
        RUN_ID, 1, feedback_bytes, max_chars=2_048
    )

    assert note.path == (
        f"research/{RUN_ID}/shard-2/{hashlib.sha256(note_bytes).hexdigest()}.json"
    )
    assert draft.path == (
        f"drafts/{RUN_ID}/revision-1/{hashlib.sha256(draft_bytes).hexdigest()}.md"
    )
    assert feedback.path == (
        f"reviews/{RUN_ID}/round-1/{hashlib.sha256(feedback_bytes).hexdigest()}.json"
    )
    assert store.read_research_note(RUN_ID, note) == note_bytes
    assert store.read_revision_draft(RUN_ID, draft) == draft_bytes
    assert store.read_review_feedback(RUN_ID, feedback) == feedback_bytes


def test_late_different_content_cannot_overwrite_completed_stage_reference(
    tmp_path: Path,
) -> None:
    tickets = tmp_path / "tickets"
    artifacts = tmp_path / "artifacts"
    tickets.mkdir()
    store = storage(tickets, artifacts)
    first_bytes = b'{"findings":[{"path":"a.txt"}]}\n'
    late_bytes = b'{"findings":[{"path":"b.txt"}]}\n'

    first = store.write_research_note(RUN_ID, 0, first_bytes, max_chars=4_096)
    late = store.write_research_note(RUN_ID, 0, late_bytes, max_chars=4_096)
    repeated = store.write_research_note(RUN_ID, 0, first_bytes, max_chars=4_096)

    assert first != late
    assert repeated == first
    assert store.read_research_note(RUN_ID, first) == first_bytes
    assert store.read_research_note(RUN_ID, late) == late_bytes


def test_stage_reads_reject_wrong_run_kind_index_hash_size_and_traversal(
    tmp_path: Path,
) -> None:
    tickets = tmp_path / "tickets"
    artifacts = tmp_path / "artifacts"
    tickets.mkdir()
    store = storage(tickets, artifacts)
    content = b'{"findings":[]}\n'
    note = store.write_research_note(RUN_ID, 0, content, max_chars=4_096)
    other_run = UUID("00000000-0000-0000-0000-000000000002")

    invalid_references = (
        (other_run, note),
        (
            RUN_ID,
            ResearchNoteRef.model_construct(
                path=note.path.replace("research/", "reviews/"),
                sha256=note.sha256,
                size_bytes=note.size_bytes,
            ),
        ),
        (
            RUN_ID,
            ResearchNoteRef.model_construct(
                path=note.path.replace("shard-0", "shard-x"),
                sha256=note.sha256,
                size_bytes=note.size_bytes,
            ),
        ),
        (
            RUN_ID,
            ResearchNoteRef.model_construct(
                path=note.path,
                sha256="b" * 64,
                size_bytes=note.size_bytes,
            ),
        ),
        (
            RUN_ID,
            ResearchNoteRef.model_construct(
                path=note.path,
                sha256=note.sha256,
                size_bytes=note.size_bytes + 1,
            ),
        ),
        (
            RUN_ID,
            ResearchNoteRef.model_construct(
                path=f"research/{RUN_ID}/shard-0/../../private.json",
                sha256=note.sha256,
                size_bytes=note.size_bytes,
            ),
        ),
    )
    for run_id, reference in invalid_references:
        with pytest.raises(StorageViolation):
            store.read_research_note(run_id, reference)


def test_stage_writes_enforce_utf8_and_character_ceilings_without_partial_files(
    tmp_path: Path,
) -> None:
    tickets = tmp_path / "tickets"
    artifacts = tmp_path / "artifacts"
    tickets.mkdir()
    store = storage(tickets, artifacts)

    with pytest.raises(StorageViolation, match="UTF-8"):
        store.write_research_note(RUN_ID, 0, b"\xff", max_chars=4_096)
    with pytest.raises(StorageViolation, match="character limit"):
        store.write_revision_draft(RUN_ID, 0, "éé".encode(), max_chars=1)
    with pytest.raises(ValueError, match="positive"):
        store.write_review_feedback(RUN_ID, 0, b"{}", max_chars=0)

    assert not any(artifacts.rglob("*.json")) if artifacts.exists() else True
    assert not any(artifacts.rglob("*.md")) if artifacts.exists() else True


def test_publish_reviewed_report_copies_only_exact_reviewed_revision(
    tmp_path: Path,
) -> None:
    tickets = tmp_path / "tickets"
    artifacts = tmp_path / "artifacts"
    tickets.mkdir()
    store = storage(tickets, artifacts)
    reviewed = store.write_revision_draft(
        RUN_ID, 2, b"# Exact reviewed report\n", max_chars=16_384
    )

    published = store.publish_reviewed_report(RUN_ID, reviewed)

    assert published.path == f"reports/{RUN_ID}/support-report.md"
    assert store.read_verified_artifact(published) == b"# Exact reviewed report\n"
    with pytest.raises(StorageViolation):
        store.publish_reviewed_report(
            RUN_ID,
            DraftRef(
                path=f"drafts/{RUN_ID}/support-report.md",
                sha256=reviewed.sha256,
                size_bytes=reviewed.size_bytes,
            ),
        )
    with pytest.raises(StorageViolation):
        store.publish_reviewed_report(
            UUID("00000000-0000-0000-0000-000000000002"), reviewed
        )


def test_version_one_draft_and_publication_paths_are_unchanged(tmp_path: Path) -> None:
    tickets = tmp_path / "tickets"
    artifacts = tmp_path / "artifacts"
    tickets.mkdir()
    store = storage(tickets, artifacts)

    draft = store.write_draft(RUN_ID, b"# Version one\n")
    published = store.publish_report(RUN_ID, draft)

    assert draft.path == f"drafts/{RUN_ID}/support-report.md"
    assert published.path == f"reports/{RUN_ID}/support-report.md"
    assert store.read_verified_artifact(published) == b"# Version one\n"
