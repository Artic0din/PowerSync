from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from scripts.support_issue_state import SupportIssueAutomation
from scripts.support_release_delivery import delivery_marker


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


REPOSITORY = "Plaintext-Lab/PowerSync"
RELEASE = {
    "tag_name": "v2.12.1100",
    "html_url": "https://github.com/Plaintext-Lab/PowerSync/releases/tag/v2.12.1100",
    "draft": False,
    "published_at": "2026-08-13T09:00:00Z",
}
PREVIOUS_RELEASE = {
    "tag_name": "v2.12.1099",
    "draft": False,
    "published_at": "2026-08-12T09:00:00Z",
}


def release_event() -> dict[str, Any]:
    return {
        "action": "published",
        "release": RELEASE,
        "repository": {"full_name": REPOSITORY},
    }


def release_dispatch_event() -> dict[str, Any]:
    return {
        "inputs": {"release_tag": RELEASE["tag_name"]},
        "repository": {"full_name": REPOSITORY},
    }


def delivery_client(check_runs: list[dict[str, Any]] | None = None) -> FakeGitHubClient:
    checks = check_runs or [
        {"name": "tests", "status": "completed", "conclusion": "success"}
    ]
    return FakeGitHubClient(
        responses={
            ("GET", f"/repos/{REPOSITORY}/releases?per_page=100&page=1"): [
                RELEASE,
                PREVIOUS_RELEASE,
            ],
            (
                "GET",
                f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100?per_page=100&page=1",
            ): {"commits": [{"sha": "merge123"}], "total_commits": 1},
            (
                "GET",
                f"/repos/{REPOSITORY}/commits/merge123/pulls?per_page=100",
            ): [
                {
                    "number": 90,
                    "html_url": "https://github.com/Plaintext-Lab/PowerSync/pull/90",
                    "body": "Root-cause fix.\n\nRefs #42",
                    "merged_at": "2026-08-13T08:00:00Z",
                    "head": {"sha": "head123"},
                }
            ],
            (
                "GET",
                f"/repos/{REPOSITORY}/commits/head123/check-runs?filter=latest&per_page=100&page=1",
            ): {"check_runs": checks, "total_count": len(checks)},
            ("GET", f"/repos/{REPOSITORY}/issues/42"): {
                "number": 42,
                "state": "open",
                "labels": [{"name": "needs investigation"}],
            },
            (
                "GET",
                f"/repos/{REPOSITORY}/issues/42/comments?per_page=100&page=1",
            ): [],
        }
    )


def test_published_release_records_linked_verified_delivery() -> None:
    client = delivery_client()

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:1"
    mutations = [request for request in client.requests if request[0] != "GET"]
    assert mutations == [
        (
            "POST",
            f"/repos/{REPOSITORY}/issues/42/labels",
            {"labels": ["awaiting confirmation"]},
        ),
        (
            "DELETE",
            f"/repos/{REPOSITORY}/issues/42/labels/needs%20investigation",
            None,
        ),
        (
            "POST",
            f"/repos/{REPOSITORY}/issues/42/comments",
            {
                "body": delivery_marker(
                    "https://github.com/Plaintext-Lab/PowerSync/pull/90",
                    "https://github.com/Plaintext-Lab/PowerSync/releases/tag/v2.12.1100",
                )
                + "\nFix delivered in "
                "https://github.com/Plaintext-Lab/PowerSync/pull/90 and released as "
                "https://github.com/Plaintext-Lab/PowerSync/releases/tag/v2.12.1100. "
                "Waiting for the reporter to confirm with `/powersync solved`."
            },
        ),
    ]


def test_workflow_dispatch_loads_and_records_the_published_release() -> None:
    client = delivery_client()
    release_path = f"/repos/{REPOSITORY}/releases/tags/{RELEASE['tag_name']}"
    client.responses[("GET", release_path)] = RELEASE

    result = SupportIssueAutomation(client).handle(release_dispatch_event())

    assert result == "deliveries-recorded:1"
    assert client.requests[0] == ("GET", release_path, None)


def test_release_time_must_be_strictly_after_merge() -> None:
    client = delivery_client()
    pull_path = f"/repos/{REPOSITORY}/commits/merge123/pulls?per_page=100"
    client.responses[("GET", pull_path)][0]["merged_at"] = RELEASE["published_at"]

    with pytest.raises(ValueError, match="after the pull request was merged"):
        SupportIssueAutomation(client).handle(release_event())

    assert all(method == "GET" for method, _, _ in client.requests)


def test_every_page_of_latest_checks_must_pass() -> None:
    first_page_path = f"/repos/{REPOSITORY}/commits/head123/check-runs?filter=latest&per_page=100&page=1"
    second_page_path = f"/repos/{REPOSITORY}/commits/head123/check-runs?filter=latest&per_page=100&page=2"
    successes = [
        {"name": f"check-{index}", "status": "completed", "conclusion": "success"}
        for index in range(100)
    ]
    client = delivery_client(successes)
    client.responses[("GET", first_page_path)]["total_count"] = 101
    client.responses[("GET", second_page_path)] = {
        "check_runs": [
            {"name": "late failure", "status": "completed", "conclusion": "failure"}
        ],
        "total_count": 101,
    }

    with pytest.raises(ValueError, match="successful check run"):
        SupportIssueAutomation(client).handle(release_event())

    assert ("GET", second_page_path, None) in client.requests
    assert all(method == "GET" for method, _, _ in client.requests)


def test_release_without_a_refs_issue_link_does_not_mutate_issues() -> None:
    client = delivery_client()
    pull_path = f"/repos/{REPOSITORY}/commits/merge123/pulls?per_page=100"
    client.responses[("GET", pull_path)][0]["body"] = "No support reference"

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:0"
    assert all(method == "GET" for method, _, _ in client.requests)


def test_refs_inside_html_comments_are_ignored() -> None:
    client = delivery_client()
    pull_path = f"/repos/{REPOSITORY}/commits/merge123/pulls?per_page=100"
    client.responses[("GET", pull_path)][0]["body"] = (
        "No support reference\n<!-- Refs #42 -->"
    )

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:0"
    assert all(method == "GET" for method, _, _ in client.requests)


def test_partial_delivery_is_repaired_without_duplicate_state_label() -> None:
    client = delivery_client()
    issue_path = f"/repos/{REPOSITORY}/issues/42"
    client.responses[("GET", issue_path)]["labels"] = [
        {"name": "awaiting confirmation"}
    ]
    client.responses[
        (
            "GET",
            f"{issue_path}/comments?per_page=100&page=1",
        )
    ] = []

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:1"
    assert (
        "POST",
        f"{issue_path}/labels",
        {"labels": ["awaiting confirmation"]},
    ) not in client.requests
    assert client.requests[-1][1] == f"{issue_path}/comments"


def test_later_release_adds_a_new_delivery_audit_comment() -> None:
    client = delivery_client()
    issue_path = f"/repos/{REPOSITORY}/issues/42"
    client.responses[("GET", issue_path)]["labels"] = [
        {"name": "awaiting confirmation"}
    ]
    client.responses[("GET", f"{issue_path}/comments?per_page=100&page=1")] = [
        {"body": "<!-- powersync-delivery:v1:previous-release -->\nOld delivery"}
    ]

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:1"
    posted_comment = client.requests[-1][2]
    assert posted_comment is not None
    assert posted_comment["body"].startswith("<!-- powersync-delivery:v1:")
    assert "previous-release" not in posted_comment["body"]
    assert RELEASE["html_url"] in posted_comment["body"]
