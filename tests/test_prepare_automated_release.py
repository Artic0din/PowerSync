from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.prepare_automated_release import (
    prepare_release_files,
    select_pull_request,
    validate_automation_reference_evidence,
    validate_automation_reference_metadata,
    validate_support_reference_evidence,
)


REPOSITORY = "Plaintext-Lab/PowerSync"


def pull_request(
    number: int,
    *,
    labels: tuple[str, ...] = ("automation",),
    repository: str = REPOSITORY,
    base_ref: str = "main",
    draft: bool = False,
    created_at: str = "2026-08-13T08:00:00Z",
) -> dict[str, Any]:
    return {
        "number": number,
        "html_url": f"https://github.com/{REPOSITORY}/pull/{number}",
        "title": f"fix(power): repair schedule (Refs #{number})",
        "created_at": created_at,
        "draft": draft,
        "labels": [{"name": label} for label in labels],
        "base": {"ref": base_ref},
        "head": {
            "ref": f"automation/{number}",
            "sha": f"sha-{number}",
            "repo": {"full_name": repository},
        },
    }


def test_queue_selects_only_the_oldest_eligible_same_repository_pull_request() -> None:
    selected = select_pull_request(
        [
            [
                pull_request(2, created_at="2026-08-13T09:00:00Z"),
                pull_request(1, created_at="2026-08-13T08:00:00Z"),
                pull_request(3, repository="contributor/fork"),
                pull_request(4, draft=True),
            ]
        ],
        REPOSITORY,
    )

    assert selected is not None
    assert selected["number"] == 1


def test_queue_waits_while_an_automation_pull_request_is_already_queued() -> None:
    selected = select_pull_request(
        [[pull_request(1, labels=("automation", "merge-queue")), pull_request(2)]],
        REPOSITORY,
    )

    assert selected is None


def test_queue_waits_when_the_queued_pull_request_lost_automation_label() -> None:
    selected = select_pull_request(
        [[pull_request(1, labels=("merge-queue",)), pull_request(2)]],
        REPOSITORY,
    )

    assert selected is None


def test_queue_waits_when_the_queued_automation_pull_request_becomes_draft() -> None:
    selected = select_pull_request(
        [
            [
                pull_request(
                    1,
                    labels=("automation", "merge-queue"),
                    draft=True,
                ),
                pull_request(2),
            ]
        ],
        REPOSITORY,
    )

    assert selected is None


def test_queue_ignores_automation_pull_requests_for_other_base_branches() -> None:
    selected = select_pull_request(
        [
            [
                pull_request(
                    1,
                    labels=("automation", "merge-queue"),
                    base_ref="release-candidate",
                ),
                pull_request(2),
            ]
        ],
        REPOSITORY,
    )

    assert selected is not None
    assert selected["number"] == 2


def test_queue_reallocates_patch_version_from_main_and_preserves_note(
    tmp_path: Path,
) -> None:
    base_manifest = tmp_path / "base-manifest.json"
    manifest = tmp_path / "manifest.json"
    release_notes = tmp_path / "RELEASE_NOTES.md"
    base_manifest.write_text('{"version":"2.12.1099"}\n', encoding="utf-8")
    manifest.write_text(
        '{"version":"2.12.1099","name":"PowerSync"}\n', encoding="utf-8"
    )
    release_notes.write_text(
        "<!-- release: v2.12.1100 -->\nFixed schedule drift.\n", encoding="utf-8"
    )

    version, issue_number = prepare_release_files(
        base_manifest,
        manifest,
        release_notes,
        "fix(power): repair schedule (Refs #42)",
        "Root-cause fix.\n\nRefs #42",
        ("fix(power): repair schedule\n\nRefs #42",),
    )

    assert version == "2.12.1100"
    assert issue_number == 42
    assert json.loads(manifest.read_text(encoding="utf-8"))["version"] == version
    assert release_notes.read_text(encoding="utf-8") == (
        "<!-- release: v2.12.1100 -->\nFixed schedule drift.\n"
    )


def test_queue_allocates_before_the_reservation_trailer_exists(
    tmp_path: Path,
) -> None:
    base_manifest = tmp_path / "base-manifest.json"
    manifest = tmp_path / "manifest.json"
    release_notes = tmp_path / "RELEASE_NOTES.md"
    base_manifest.write_text('{"version":"2.12.1099"}\n', encoding="utf-8")
    manifest.write_text('{"version":"2.12.1099"}\n', encoding="utf-8")
    release_notes.write_text(
        "<!-- release: v2.12.1099 -->\nFixed schedule drift.\n",
        encoding="utf-8",
    )

    version, issue_number = prepare_release_files(
        base_manifest,
        manifest,
        release_notes,
        "fix(power): repair schedule (Refs #42)",
        "Root-cause fix.\n\nRefs #42",
        ("fix(power): repair schedule",),
    )

    assert (version, issue_number) == ("2.12.1100", 42)


@pytest.mark.parametrize(
    "title",
    ["fix(power): missing reference", "fix(power): mixed (Refs #1, Refs #2)"],
)
def test_queue_rejects_ambiguous_issue_references(tmp_path: Path, title: str) -> None:
    base_manifest = tmp_path / "base-manifest.json"
    manifest = tmp_path / "manifest.json"
    release_notes = tmp_path / "RELEASE_NOTES.md"
    base_manifest.write_text('{"version":"2.12.1099"}\n', encoding="utf-8")
    manifest.write_text('{"version":"2.12.1100"}\n', encoding="utf-8")
    release_notes.write_text(
        "<!-- release: v2.12.1100 -->\nFixed schedule drift.\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="exactly one Refs"):
        prepare_release_files(
            base_manifest,
            manifest,
            release_notes,
            title,
            "Root-cause fix.\n\nRefs #42",
            ("fix(power): repair schedule\n\nRefs #42",),
        )


def test_support_reference_must_be_preserved_in_a_commit_message() -> None:
    with pytest.raises(ValueError, match="Refs #42"):
        validate_support_reference_evidence(
            "Root-cause fix.\n\nRefs #42",
            ("fix(power): repair schedule",),
        )

    validate_support_reference_evidence(
        "Root-cause fix.\n\nRefs #42",
        ("fix(power): repair schedule\n\nRefs #42",),
    )


def test_automation_title_reference_must_match_body_and_commit_evidence() -> None:
    with pytest.raises(ValueError, match="same support issue"):
        validate_automation_reference_evidence(
            "fix(power): repair schedule (Refs #81)",
            "Root-cause fix.\n\nRefs #80",
            ("fix(power): repair schedule\n\nRefs #80",),
        )

    issue_number = validate_automation_reference_evidence(
        "fix(power): repair schedule (Refs #80)",
        "Root-cause fix.\n\nRefs #80",
        ("fix(power): repair schedule\n\nRefs #80",),
    )

    assert issue_number == 80


def test_initial_automation_validation_binds_title_to_body_before_reservation() -> None:
    issue_number = validate_automation_reference_metadata(
        "fix(power): repair schedule (Refs #80)",
        "Root-cause fix.\n\nRefs #80",
    )

    assert issue_number == 80
    with pytest.raises(ValueError, match="title and body"):
        validate_automation_reference_metadata(
            "fix(power): repair schedule (Refs #81)",
            "Root-cause fix.\n\nRefs #80",
        )


def test_support_reference_requires_a_terminal_metadata_line() -> None:
    validate_support_reference_evidence(
        "Root-cause fix.\n\nRefs #42",
        (
            "docs: show examples\n\n```text\nRefs #42\n```\n\n"
            "Refs #42",
        ),
    )

    with pytest.raises(ValueError, match="Refs #42"):
        validate_support_reference_evidence(
            "Root-cause fix.\n\nRefs #42",
            ("docs: show example\n\n```text\nRefs #42\n```",),
        )


@pytest.mark.parametrize(
    ("body", "commits"),
    [
        ("Root-cause fix.\n\nRefs #42", ("fix: repair\n\nRefs #42\nRefs #43",)),
        ("Root-cause fix.", ("fix: repair\n\nRefs #42",)),
    ],
)
def test_mutable_and_immutable_support_references_must_match_exactly(
    body: str, commits: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="match exactly"):
        validate_support_reference_evidence(body, commits)


def test_commented_support_reference_does_not_require_merge_evidence() -> None:
    validate_support_reference_evidence(
        "No support issue.\n<!-- Refs #42",
        ("docs: update support guidance",),
    )
