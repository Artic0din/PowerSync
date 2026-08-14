"""Select and prepare one automation pull request for the merge queue."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Sequence

AUTOMATION_LABEL = "automation"
MERGE_QUEUE_LABEL = "merge-queue"
RELEASE_BRANCH = "main"
REFERENCE_PATTERN = re.compile(r"(?i)\bRefs\s+#(\d+)\b")
HTML_COMMENT_PATTERN = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
RELEASE_MARKER_PATTERN = re.compile(
    r"^\s*<!--\s*release:\s*v?\d+\.\d+\.\d+\s*-->\s*", re.IGNORECASE
)


def select_pull_request(pages: Any, repository: str) -> dict[str, Any] | None:
    """Return the oldest eligible PR unless another automation PR is queued."""
    if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
        raise ValueError("GitHub returned invalid pull request pages")
    pull_requests = [pull_request for page in pages for pull_request in page]
    automation_pull_requests = [
        pull_request
        for pull_request in pull_requests
        if _is_release_branch_automation_pull_request(pull_request, repository)
    ]
    if any(
        MERGE_QUEUE_LABEL in _label_names(pull_request)
        for pull_request in automation_pull_requests
    ):
        return None
    eligible = [
        pull_request
        for pull_request in automation_pull_requests
        if _is_eligible_pull_request(pull_request, repository)
    ]
    return min(eligible, key=lambda item: item.get("created_at", ""), default=None)


def prepare_release_files(
    base_manifest_path: Path,
    manifest_path: Path,
    release_notes_path: Path,
    pull_request_title: str,
) -> tuple[str, int]:
    """Allocate the next patch from main and retain the reviewed release note."""
    issue_numbers = {
        int(number) for number in REFERENCE_PATTERN.findall(pull_request_title)
    }
    if len(issue_numbers) != 1:
        raise ValueError("The pull request title must contain exactly one Refs #N")
    issue_number = issue_numbers.pop()

    base_manifest = _read_manifest(base_manifest_path)
    manifest = _read_manifest(manifest_path)
    base_version = _parse_version(base_manifest.get("version"))
    next_version = f"{base_version[0]}.{base_version[1]}.{base_version[2] + 1}"

    release_notes = release_notes_path.read_text(encoding="utf-8")
    marker = RELEASE_MARKER_PATTERN.match(release_notes)
    if marker is None:
        raise ValueError("RELEASE_NOTES.md must start with a release version marker")
    note = release_notes[marker.end() :].strip()
    if not note:
        raise ValueError("RELEASE_NOTES.md must contain a user-facing release note")

    manifest["version"] = next_version
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    release_notes_path.write_text(
        f"<!-- release: v{next_version} -->\n{note}\n", encoding="utf-8"
    )
    return next_version, issue_number


def validate_support_reference_evidence(
    pull_request_body: Any, commit_messages: Sequence[str]
) -> None:
    """Require visible support references to survive in immutable merge evidence."""
    if not isinstance(pull_request_body, str):
        return
    visible_body = HTML_COMMENT_PATTERN.sub("", pull_request_body)
    body_references = {
        int(number) for number in REFERENCE_PATTERN.findall(visible_body)
    }
    if not body_references:
        return
    commit_references = {
        int(number)
        for message in commit_messages
        for number in REFERENCE_PATTERN.findall(message)
    }
    missing_references = sorted(body_references - commit_references)
    if missing_references:
        missing = ", ".join(f"Refs #{number}" for number in missing_references)
        raise ValueError(
            f"Support references must be preserved in commit messages: {missing}"
        )


def _is_eligible_pull_request(pull_request: Any, repository: str) -> bool:
    if (
        not _is_release_branch_automation_pull_request(pull_request, repository)
        or pull_request.get("draft") is not False
    ):
        return False
    head = pull_request.get("head")
    return (
        isinstance(pull_request.get("number"), int)
        and isinstance(pull_request.get("html_url"), str)
        and bool(pull_request["html_url"])
        and isinstance(pull_request.get("title"), str)
        and bool(pull_request["title"])
        and isinstance(pull_request.get("created_at"), str)
        and bool(pull_request["created_at"])
        and isinstance(head, dict)
        and isinstance(head.get("ref"), str)
        and bool(head["ref"])
        and isinstance(head.get("sha"), str)
        and bool(head["sha"])
    )


def _is_release_branch_automation_pull_request(
    pull_request: Any, repository: str
) -> bool:
    if not isinstance(pull_request, dict):
        return False
    head = pull_request.get("head")
    head_repository = head.get("repo") if isinstance(head, dict) else None
    base = pull_request.get("base")
    return (
        isinstance(head_repository, dict)
        and head_repository.get("full_name", "").casefold() == repository.casefold()
        and isinstance(base, dict)
        and base.get("ref") == RELEASE_BRANCH
        and AUTOMATION_LABEL in _label_names(pull_request)
    )


def _label_names(pull_request: dict[str, Any]) -> set[str]:
    return {
        label.get("name", "")
        for label in pull_request.get("labels", [])
        if isinstance(label, dict)
    }


def _read_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"{path} is not a JSON object")
    return manifest


def _parse_version(value: Any) -> tuple[int, int, int]:
    if not isinstance(value, str) or re.fullmatch(r"\d+\.\d+\.\d+", value) is None:
        raise ValueError("The default-branch manifest version is not x.y.z")
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def _select_command(arguments: argparse.Namespace) -> None:
    pages = json.loads(arguments.pages.read_text(encoding="utf-8"))
    pull_request = select_pull_request(pages, arguments.repository)
    if pull_request is None:
        print("selected=false")
        return
    head = pull_request["head"]
    print("selected=true")
    print(f"number={pull_request['number']}")
    print(f"url={pull_request['html_url']}")
    print(f"head_ref={head['ref']}")
    print(f"head_sha={head['sha']}")
    print(f"title={pull_request['title']}")


def _prepare_command(arguments: argparse.Namespace) -> None:
    version, issue_number = prepare_release_files(
        arguments.base_manifest,
        arguments.manifest,
        arguments.release_notes,
        arguments.title,
    )
    print(f"version={version}")
    print(f"issue_number={issue_number}")


def _validate_reference_command(arguments: argparse.Namespace) -> None:
    event = json.loads(arguments.event.read_text(encoding="utf-8"))
    pages = json.loads(arguments.pages.read_text(encoding="utf-8"))
    pull_request = event.get("pull_request") if isinstance(event, dict) else None
    if not isinstance(pull_request, dict):
        raise ValueError("The event has no pull request")
    if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
        raise ValueError("GitHub returned invalid pull request commit pages")
    messages: list[str] = []
    for page in pages:
        for item in page:
            commit = item.get("commit") if isinstance(item, dict) else None
            message = commit.get("message") if isinstance(commit, dict) else None
            if not isinstance(message, str) or not message:
                raise ValueError("A pull request commit has no message")
            messages.append(message)
    validate_support_reference_evidence(pull_request.get("body"), tuple(messages))
    print("support-reference-evidence=valid")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    select_parser = subparsers.add_parser("select")
    select_parser.add_argument("--pages", type=Path, required=True)
    select_parser.add_argument("--repository", required=True)
    select_parser.set_defaults(handler=_select_command)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--base-manifest", type=Path, required=True)
    prepare_parser.add_argument("--manifest", type=Path, required=True)
    prepare_parser.add_argument("--release-notes", type=Path, required=True)
    prepare_parser.add_argument("--title", required=True)
    prepare_parser.set_defaults(handler=_prepare_command)

    validate_reference_parser = subparsers.add_parser("validate-reference")
    validate_reference_parser.add_argument("--event", type=Path, required=True)
    validate_reference_parser.add_argument("--pages", type=Path, required=True)
    validate_reference_parser.set_defaults(handler=_validate_reference_command)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv)
    arguments.handler(arguments)


if __name__ == "__main__":
    main()
