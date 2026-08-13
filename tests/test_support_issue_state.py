from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError

import pytest

from scripts.run_support_issue_state import GitHubClient
from scripts.support_issue_state import Command, SupportIssueAutomation, parse_command


@dataclass
class FakeGitHubClient:
    responses: dict[tuple[str, str], Any] = field(default_factory=dict)
    requests: list[tuple[str, str, dict[str, Any] | None]] = field(default_factory=list)

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        self.requests.append((method, path, payload))
        return self.responses.get((method, path), {})


def solved_event(
    body: str = "/powersync solved", actor: str = "reporter"
) -> dict[str, Any]:
    return {
        "action": "created",
        "comment": {"body": body, "user": {"login": actor}},
        "issue": {"number": 42, "pull_request": None},
        "repository": {"full_name": "Plaintext-Lab/PowerSync"},
    }


def live_issue(*, state: str = "open", labels: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "number": 42,
        "state": state,
        "user": {"login": "reporter"},
        "labels": [{"name": label} for label in labels],
    }


def test_only_exact_solved_command_is_recognised() -> None:
    assert parse_command("/powersync solved") == Command(action="solved")
    assert parse_command("This looks solved now") is None
    assert parse_command("/powersync solved thanks") is None
    assert parse_command("/powersync delivered https://example.com") is None


def test_reporter_confirmation_uses_live_issue_state() -> None:
    issue_path = "/repos/Plaintext-Lab/PowerSync/issues/42"
    client = FakeGitHubClient(
        responses={
            ("GET", issue_path): live_issue(labels=("awaiting confirmation",)),
            ("GET", f"{issue_path}/comments?per_page=100&page=1"): [],
        }
    )

    result = SupportIssueAutomation(client).handle(solved_event())

    assert result == "closed-confirmed"
    assert client.requests[0] == ("GET", issue_path, None)
    assert ("POST", f"{issue_path}/labels", {"labels": ["solved"]}) in client.requests
    assert (
        "PATCH",
        issue_path,
        {"state": "closed", "state_reason": "completed"},
    ) in client.requests
    assert client.requests[-1] == (
        "DELETE",
        f"{issue_path}/labels/awaiting%20confirmation",
        None,
    )


def test_non_author_without_repository_access_is_rejected_cleanly() -> None:
    issue_path = "/repos/Plaintext-Lab/PowerSync/issues/42"
    permission_path = "/repos/Plaintext-Lab/PowerSync/collaborators/outsider/permission"
    client = FakeGitHubClient(
        responses={
            ("GET", issue_path): live_issue(labels=("awaiting confirmation",)),
            ("GET", permission_path): {},
        }
    )

    result = SupportIssueAutomation(client).handle(solved_event(actor="outsider"))

    assert result == "unauthorized"
    assert client.requests[-1] == ("GET", permission_path, None)
    assert all(method == "GET" for method, _, _ in client.requests)


def test_partial_confirmation_is_retryable_without_duplicate_comment() -> None:
    issue_path = "/repos/Plaintext-Lab/PowerSync/issues/42"
    audit_marker = "<!-- powersync-resolution:v1 -->"
    client = FakeGitHubClient(
        responses={
            ("GET", issue_path): live_issue(labels=("awaiting confirmation", "solved")),
            ("GET", f"{issue_path}/comments?per_page=100&page=1"): [
                {"body": f"{audit_marker}\nEarlier audit"}
            ],
        }
    )

    result = SupportIssueAutomation(client).handle(solved_event())

    assert result == "closed-confirmed"
    assert not any(
        path.endswith("/comments") and method == "POST"
        for method, path, _ in client.requests
    )
    assert client.requests[-1][1].endswith("/labels/awaiting%20confirmation")


def test_conversational_wording_performs_no_api_requests() -> None:
    client = FakeGitHubClient()

    result = SupportIssueAutomation(client).handle(
        solved_event("Thanks, this is solved")
    )

    assert result == "ignored"
    assert client.requests == []


def test_missing_collaborator_permission_is_treated_as_no_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_permission(*_args: Any, **_kwargs: Any) -> Any:
        raise HTTPError(
            "https://api.github.com/repos/example/repo/collaborators/outsider/permission",
            404,
            "Not Found",
            {},
            io.BytesIO(b'{"message":"Not Found"}'),
        )

    monkeypatch.setattr("scripts.run_support_issue_state.urlopen", missing_permission)

    result = GitHubClient("test-token").request(
        "GET", "/repos/example/repo/collaborators/outsider/permission"
    )

    assert result == {}
