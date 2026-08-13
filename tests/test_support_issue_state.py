from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from scripts.support_issue_state import (
    Command,
    SupportIssueAutomation,
    parse_command,
)


@dataclass
class FakeGitHubClient:
    responses: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    requests: list[tuple[str, str, dict[str, Any] | None]] = field(default_factory=list)

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.requests.append((method, path, payload))
        return self.responses.get((method, path), {})


def issue_event(
    body: str,
    *,
    actor: str = "reporter",
    author: str = "reporter",
    labels: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "action": "created",
        "comment": {"body": body, "user": {"login": actor}},
        "issue": {
            "number": 42,
            "state": "open",
            "user": {"login": author},
            "labels": [{"name": label} for label in labels],
        },
        "repository": {"full_name": "Plaintext-Lab/PowerSync"},
    }


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("/powersync solved", Command(action="solved")),
        (
            "/powersync delivered https://github.com/Plaintext-Lab/PowerSync/pull/90 "
            "https://github.com/bolagnaise/PowerSync/releases/tag/v2.12.1100",
            Command(
                action="delivered",
                pull_request_url="https://github.com/Plaintext-Lab/PowerSync/pull/90",
                release_url="https://github.com/bolagnaise/PowerSync/releases/tag/v2.12.1100",
            ),
        ),
        ("This looks solved now", None),
        ("/powersync solved thanks", None),
        ("Please run /powersync solved", None),
    ],
)
def test_parse_command_requires_an_exact_command(
    body: str,
    expected: Command | None,
) -> None:
    assert parse_command(body) == expected


def test_reporter_can_confirm_an_awaiting_issue() -> None:
    client = FakeGitHubClient()
    automation = SupportIssueAutomation(client)

    result = automation.handle(
        issue_event("/powersync solved", labels=("awaiting confirmation",))
    )

    assert result == "closed-confirmed"
    assert client.requests == [
        (
            "POST",
            "/repos/Plaintext-Lab/PowerSync/issues/42/labels",
            {"labels": ["solved"]},
        ),
        (
            "DELETE",
            "/repos/Plaintext-Lab/PowerSync/issues/42/labels/awaiting%20confirmation",
            None,
        ),
        (
            "POST",
            "/repos/Plaintext-Lab/PowerSync/issues/42/comments",
            {
                "body": "Resolution confirmed by @reporter using `/powersync solved`. "
                "Closing this issue as completed."
            },
        ),
        (
            "PATCH",
            "/repos/Plaintext-Lab/PowerSync/issues/42",
            {"state": "closed", "state_reason": "completed"},
        ),
    ]


def test_solved_command_is_ignored_before_delivery_confirmation() -> None:
    client = FakeGitHubClient()
    automation = SupportIssueAutomation(client)

    result = automation.handle(issue_event("/powersync solved"))

    assert result == "invalid-state"
    assert client.requests == []


def test_non_author_requires_write_permission_to_confirm_resolution() -> None:
    permission_path = "/repos/Plaintext-Lab/PowerSync/collaborators/outsider/permission"
    client = FakeGitHubClient(
        responses={("GET", permission_path): {"permission": "read"}}
    )
    automation = SupportIssueAutomation(client)

    result = automation.handle(
        issue_event(
            "/powersync solved",
            actor="outsider",
            labels=("awaiting confirmation",),
        )
    )

    assert result == "unauthorized"
    assert client.requests == [("GET", permission_path, None)]


def test_maintainer_can_confirm_an_awaiting_issue() -> None:
    permission_path = (
        "/repos/Plaintext-Lab/PowerSync/collaborators/maintainer/permission"
    )
    client = FakeGitHubClient(
        responses={("GET", permission_path): {"permission": "write"}}
    )
    automation = SupportIssueAutomation(client)

    result = automation.handle(
        issue_event(
            "/powersync solved",
            actor="maintainer",
            labels=("awaiting confirmation",),
        )
    )

    assert result == "closed-confirmed"
    assert client.requests[0] == ("GET", permission_path, None)
    assert client.requests[-1] == (
        "PATCH",
        "/repos/Plaintext-Lab/PowerSync/issues/42",
        {"state": "closed", "state_reason": "completed"},
    )


def test_maintainer_can_mark_a_verified_delivery() -> None:
    permission_path = (
        "/repos/Plaintext-Lab/PowerSync/collaborators/maintainer/permission"
    )
    pull_path = "/repos/Plaintext-Lab/PowerSync/pulls/90"
    checks_path = (
        "/repos/Plaintext-Lab/PowerSync/commits/abc123/check-runs?filter=latest"
    )
    release_path = "/repos/bolagnaise/PowerSync/releases/tags/v2.12.1100"
    client = FakeGitHubClient(
        responses={
            ("GET", permission_path): {"permission": "write"},
            ("GET", pull_path): {
                "html_url": "https://github.com/Plaintext-Lab/PowerSync/pull/90",
                "merged_at": "2026-08-13T08:00:00Z",
                "head": {"sha": "abc123"},
            },
            ("GET", checks_path): {
                "check_runs": [
                    {"name": "tests", "status": "completed", "conclusion": "success"},
                    {"name": "lint", "status": "completed", "conclusion": "skipped"},
                ]
            },
            ("GET", release_path): {
                "html_url": "https://github.com/bolagnaise/PowerSync/releases/tag/v2.12.1100",
                "tag_name": "v2.12.1100",
                "draft": False,
                "published_at": "2026-08-13T09:00:00Z",
            },
        }
    )
    automation = SupportIssueAutomation(client)

    result = automation.handle(
        issue_event(
            "/powersync delivered https://github.com/Plaintext-Lab/PowerSync/pull/90 "
            "https://github.com/bolagnaise/PowerSync/releases/tag/v2.12.1100",
            actor="maintainer",
            labels=("agent ready",),
        )
    )

    assert result == "awaiting-confirmation"
    assert client.requests[-3:] == [
        (
            "POST",
            "/repos/Plaintext-Lab/PowerSync/issues/42/labels",
            {"labels": ["awaiting confirmation"]},
        ),
        (
            "DELETE",
            "/repos/Plaintext-Lab/PowerSync/issues/42/labels/agent%20ready",
            None,
        ),
        (
            "POST",
            "/repos/Plaintext-Lab/PowerSync/issues/42/comments",
            {
                "body": "Delivery recorded by @maintainer. Fix: "
                "https://github.com/Plaintext-Lab/PowerSync/pull/90. Release: "
                "https://github.com/bolagnaise/PowerSync/releases/tag/v2.12.1100. "
                "Waiting for the reporter or a maintainer to confirm with "
                "`/powersync solved`."
            },
        ),
    ]


def test_delivery_is_rejected_without_successful_checks() -> None:
    permission_path = (
        "/repos/Plaintext-Lab/PowerSync/collaborators/maintainer/permission"
    )
    pull_path = "/repos/Plaintext-Lab/PowerSync/pulls/90"
    checks_path = (
        "/repos/Plaintext-Lab/PowerSync/commits/abc123/check-runs?filter=latest"
    )
    client = FakeGitHubClient(
        responses={
            ("GET", permission_path): {"permission": "admin"},
            ("GET", pull_path): {
                "merged_at": "2026-08-13T08:00:00Z",
                "head": {"sha": "abc123"},
            },
            ("GET", checks_path): {"check_runs": []},
        }
    )
    automation = SupportIssueAutomation(client)

    with pytest.raises(ValueError, match="successful check run"):
        automation.handle(
            issue_event(
                "/powersync delivered https://github.com/Plaintext-Lab/PowerSync/pull/90 "
                "https://github.com/bolagnaise/PowerSync/releases/tag/v2.12.1100",
                actor="maintainer",
            )
        )

    assert all(method == "GET" for method, _, _ in client.requests)


def test_release_must_be_published_after_the_fix_was_merged() -> None:
    permission_path = (
        "/repos/Plaintext-Lab/PowerSync/collaborators/maintainer/permission"
    )
    pull_path = "/repos/Plaintext-Lab/PowerSync/pulls/90"
    checks_path = (
        "/repos/Plaintext-Lab/PowerSync/commits/abc123/check-runs?filter=latest"
    )
    release_path = "/repos/bolagnaise/PowerSync/releases/tags/v2.12.1100"
    client = FakeGitHubClient(
        responses={
            ("GET", permission_path): {"permission": "maintain"},
            ("GET", pull_path): {
                "merged_at": "2026-08-13T10:00:00Z",
                "head": {"sha": "abc123"},
            },
            ("GET", checks_path): {
                "check_runs": [
                    {"name": "tests", "status": "completed", "conclusion": "success"}
                ]
            },
            ("GET", release_path): {
                "tag_name": "v2.12.1100",
                "draft": False,
                "published_at": "2026-08-13T09:00:00Z",
            },
        }
    )
    automation = SupportIssueAutomation(client)

    with pytest.raises(ValueError, match="after the pull request was merged"):
        automation.handle(
            issue_event(
                "/powersync delivered https://github.com/Plaintext-Lab/PowerSync/pull/90 "
                "https://github.com/bolagnaise/PowerSync/releases/tag/v2.12.1100",
                actor="maintainer",
            )
        )

    assert all(method == "GET" for method, _, _ in client.requests)
