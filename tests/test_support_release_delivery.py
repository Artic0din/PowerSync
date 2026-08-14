from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.support_issue_state import SupportIssueAutomation
from scripts.support_release_delivery import ReleaseDelivery, delivery_marker


@dataclass
class FakeGitHubClient:
    responses: dict[tuple[str, str], Any] = field(default_factory=dict)
    response_sequences: dict[tuple[str, str], list[Any]] = field(default_factory=dict)
    requests: list[tuple[str, str, dict[str, Any] | None]] = field(default_factory=list)

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        self.requests.append((method, path, payload))
        sequence = self.response_sequences.get((method, path))
        if sequence:
            return sequence.pop(0)
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
                f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100",
            ): {"status": "ahead"},
            (
                "GET",
                f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100?per_page=100&page=1",
            ): {
                "commits": [
                    {
                        "sha": "merge123",
                        "commit": {
                            "message": "fix(power): correct schedule\n\nRefs #42"
                        },
                    }
                ],
                "total_commits": 1,
            },
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
            ("POST", f"/repos/{REPOSITORY}/issues/42/comments"): {"id": 987},
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


def test_missing_referenced_issue_does_not_abort_other_deliveries() -> None:
    client = delivery_client()
    compare_path = (
        f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100?per_page=100&page=1"
    )
    client.responses[("GET", compare_path)]["commits"][0]["commit"]["message"] = (
        "fix(power): correct schedule\n\nRefs #999999\nRefs #42"
    )

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:1"
    assert ("GET", f"/repos/{REPOSITORY}/issues/999999", None) in client.requests
    assert any(
        method == "POST" and path == f"/repos/{REPOSITORY}/issues/42/comments"
        for method, path, _ in client.requests
    )


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


def test_release_without_a_refs_commit_link_does_not_mutate_issues() -> None:
    client = delivery_client()
    compare_path = (
        f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100?per_page=100&page=1"
    )
    client.responses[("GET", compare_path)]["commits"][0]["commit"]["message"] = (
        "No support reference"
    )

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:0"
    assert all(method == "GET" for method, _, _ in client.requests)


def test_refs_inside_html_comments_are_ignored() -> None:
    client = delivery_client()
    compare_path = (
        f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100?per_page=100&page=1"
    )
    client.responses[("GET", compare_path)]["commits"][0]["commit"]["message"] = (
        "No support reference\n<!-- Refs #42 -->"
    )

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:0"
    assert all(method == "GET" for method, _, _ in client.requests)


def test_refs_inside_unterminated_html_comments_are_ignored() -> None:
    client = delivery_client()
    compare_path = (
        f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100?per_page=100&page=1"
    )
    client.responses[("GET", compare_path)]["commits"][0]["commit"]["message"] = (
        "No support reference\n<!-- Refs #42"
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
    assert any(
        method == "POST" and path == f"{issue_path}/comments"
        for method, path, _ in client.requests
    )


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
    posted_comment = next(
        payload
        for method, path, payload in client.requests
        if method == "POST" and path == f"{issue_path}/comments"
    )
    assert posted_comment is not None
    assert posted_comment["body"].startswith("<!-- powersync-delivery:v1:")
    assert "previous-release" not in posted_comment["body"]
    assert RELEASE["html_url"] in posted_comment["body"]


def test_pull_request_reference_is_not_treated_as_a_support_issue() -> None:
    client = delivery_client()
    issue_path = f"/repos/{REPOSITORY}/issues/42"
    client.responses[("GET", issue_path)]["pull_request"] = {
        "url": "https://api.github.com/repos/Plaintext-Lab/PowerSync/pulls/42"
    }

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:0"
    assert all(method == "GET" for method, _, _ in client.requests)


def test_reporter_cannot_spoof_a_delivery_marker() -> None:
    client = delivery_client()
    issue_path = f"/repos/{REPOSITORY}/issues/42"
    marker = delivery_marker(
        "https://github.com/Plaintext-Lab/PowerSync/pull/90",
        str(RELEASE["html_url"]),
    )
    client.responses[("GET", f"{issue_path}/comments?per_page=100&page=1")] = [
        {"body": marker, "user": {"login": "reporter"}}
    ]

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:1"
    assert any(
        request[0:2] == ("POST", f"{issue_path}/comments")
        for request in client.requests
    )


def test_workflow_marker_deduplicates_the_same_delivery() -> None:
    client = delivery_client()
    issue_path = f"/repos/{REPOSITORY}/issues/42"
    client.responses[("GET", issue_path)]["labels"] = [
        {"name": "awaiting confirmation"}
    ]
    marker = delivery_marker(
        "https://github.com/Plaintext-Lab/PowerSync/pull/90",
        str(RELEASE["html_url"]),
    )
    client.responses[("GET", f"{issue_path}/comments?per_page=100&page=1")] = [
        {"body": marker, "user": {"login": "github-actions[bot]"}}
    ]

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:0"
    assert all(method == "GET" for method, _, _ in client.requests)


def test_comment_limit_fails_before_delivery_state_mutation() -> None:
    client = delivery_client()
    issue_path = f"/repos/{REPOSITORY}/issues/42"
    full_page = [{"body": "ordinary comment", "user": {"login": "reporter"}}] * 100
    for page in range(1, 11):
        client.responses[
            ("GET", f"{issue_path}/comments?per_page=100&page={page}")
        ] = full_page

    with pytest.raises(
        ValueError, match="Issue comments exceed the supported pagination limit"
    ):
        SupportIssueAutomation(client).handle(release_event())

    assert all(method == "GET" for method, _, _ in client.requests)


def test_release_workflow_reconciles_an_existing_published_release() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "checks: read" in workflow.split("jobs:", 1)[0]
    assert "Resolve published release tag" in workflow
    assert "steps.support_release.outputs.tag != ''" in workflow
    assert 'release_tag="${{ steps.support_release.outputs.tag }}"' in workflow
    assert "id: support_reconciliation" in workflow
    assert "continue-on-error: true" in workflow
    assert "steps.support_reconciliation.outcome == 'failure'" in workflow
    assert "gh run watch \"$RUN_ID\" --exit-status --compact" in workflow


def test_release_workflow_rejects_drafts_as_existing_publications() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "--json tagName,isDraft,publishedAt" in workflow
    assert ".isDraft == false and .publishedAt != null" in workflow


def test_release_workflow_verifies_published_tag_targets_workflow_head() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    published_check = workflow.index('if [ -n "$PUBLISHED_TAG" ]')
    release_exists = workflow.index("release_exists=true", published_check)
    verification = workflow.index(
        'git rev-parse "$PUBLISHED_TAG^{commit}"', published_check
    )

    assert verification < release_exists
    assert "not workflow HEAD" in workflow[verification:release_exists]


def test_release_workflow_repairs_a_tag_without_a_published_release() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "id: check_release" in workflow
    assert "release_exists=false" in workflow
    assert "Create or repair tag and release" in workflow
    assert 'TAG="${{ steps.check_release.outputs.tag }}"' in workflow
    assert "if: steps.check_release.outputs.release_exists == 'false'" in workflow


def test_release_workflow_verifies_existing_tag_targets_before_publish() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "HEAD_SHA=$(git rev-parse HEAD)" in workflow
    assert 'tag_sha=$(git rev-parse "$candidate^{commit}")' in workflow
    assert 'if [ "$tag_sha" != "$HEAD_SHA" ]' in workflow
    assert 'DRAFT_TARGET_ARGS=(--target "$HEAD_SHA")' in workflow
    assert workflow.index("verify_tag_targets_head") < workflow.index(
        'gh release edit "$DRAFT_TAG"'
    )


def test_release_events_check_out_the_current_default_branch_state_machine() -> None:
    workflow = Path(".github/workflows/support-issue-state.yml").read_text(
        encoding="utf-8"
    )

    assert "ref: ${{ github.event.repository.default_branch }}" in workflow


def test_automation_queue_serializes_release_allocation_and_records_refs() -> None:
    workflow = Path(".github/workflows/queue-automated-fixes.yml").read_text(
        encoding="utf-8"
    )

    assert "group: automated-fix-merge-queue" in workflow
    assert "ref: main" in workflow
    assert "prepare_automated_release.py prepare" in workflow
    assert "Trusted queue logic is not available on main yet" in workflow
    assert workflow.index("Trusted queue logic is not available on main yet") < workflow.index(
        "prepare_automated_release.py select"
    )
    assert "pulls?state=open&base=main&per_page=100" in workflow
    assert "git commit --allow-empty \\" in workflow
    assert '-m "Refs #$ISSUE_NUMBER"' in workflow
    assert "Verify squash merge preserves delivery evidence" in workflow
    assert 'squash_merge_commit_message == "COMMIT_MESSAGES"' in workflow
    assert 'git log -1 --format=%B' in workflow
    assert workflow.index("Record immutable delivery evidence") < workflow.index(
        "Enter the protected Graphite merge queue"
    )
    assert (
        "types: [opened, labeled, unlabeled, ready_for_review, synchronize, closed]"
        in workflow
    )
    assert "github.event.pull_request.merged == false" in workflow
    assert "workflow_dispatch:" in workflow


def test_pull_request_validation_requires_immutable_support_references() -> None:
    workflow = Path(".github/workflows/validate.yml").read_text(encoding="utf-8")

    assert "Validate immutable support references" in workflow
    assert "validate-reference" in workflow
    assert 'squash_merge_commit_message == "COMMIT_MESSAGES"' in workflow
    assert "types: [opened, synchronize, reopened, ready_for_review, edited]" in workflow


def test_solved_comment_filter_defers_whitespace_handling_to_the_parser() -> None:
    workflow = Path(".github/workflows/support-issue-state.yml").read_text(
        encoding="utf-8"
    )

    assert "github.event.issue.pull_request == null" in workflow
    assert "github.event.comment.body == '/powersync solved'" not in workflow


def test_release_advances_the_queue_only_after_successful_completion() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    advance = workflow.index("Advance the automated fix queue after release completion")
    assert workflow.index("Clear release notes and record Discord marker") < advance
    assert workflow.index("Report support reconciliation failure") < advance
    assert (
        "if: ${{ success() && steps.support_release.outputs.tag != '' }}"
        in workflow[advance:]
    )
    assert "gh workflow run queue-automated-fixes.yml --ref main" in workflow[advance:]


def test_release_rechecks_issue_state_before_posting_delivery() -> None:
    client = delivery_client()
    issue_path = f"/repos/{REPOSITORY}/issues/42"
    client.response_sequences[("GET", issue_path)] = [
        {
            "number": 42,
            "state": "open",
            "labels": [{"name": "needs investigation"}],
        },
        {
            "number": 42,
            "state": "closed",
            "labels": [{"name": "solved"}],
        },
    ]

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:0"
    assert sum(request[0:2] == ("GET", issue_path) for request in client.requests) == 2
    assert not any(
        method == "POST" and path.endswith("/comments")
        for method, path, _ in client.requests
    )


def test_delivery_comment_is_rolled_back_after_concurrent_confirmation() -> None:
    client = delivery_client()
    issue_path = f"/repos/{REPOSITORY}/issues/42"
    client.response_sequences[("GET", issue_path)] = [
        {
            "number": 42,
            "state": "open",
            "labels": [{"name": "needs investigation"}],
        },
        {
            "number": 42,
            "state": "open",
            "labels": [{"name": "awaiting confirmation"}],
        },
        {
            "number": 42,
            "state": "closed",
            "labels": [{"name": "solved"}],
        },
    ]

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:0"
    assert (
        "DELETE",
        f"/repos/{REPOSITORY}/issues/comments/987",
        None,
    ) in client.requests
    assert (
        "DELETE",
        f"{issue_path}/labels/awaiting%20confirmation",
        None,
    ) in client.requests


def test_previous_release_search_scans_every_bounded_page() -> None:
    client = FakeGitHubClient()
    first_page = [
        {
            "tag_name": "v2.12.1098",
            "draft": False,
            "published_at": "2026-08-11T09:00:00Z",
        }
    ] + [{"draft": True} for _ in range(99)]
    client.responses[("GET", f"/repos/{REPOSITORY}/releases?per_page=100&page=1")] = (
        first_page
    )
    client.responses[("GET", f"/repos/{REPOSITORY}/releases?per_page=100&page=2")] = [
        PREVIOUS_RELEASE
    ]
    client.responses[
        ("GET", f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100")
    ] = {"status": "ahead"}

    previous_tag = ReleaseDelivery(client)._find_previous_release(
        REPOSITORY,
        str(RELEASE["tag_name"]),
        datetime.fromisoformat(str(RELEASE["published_at"]).replace("Z", "+00:00")),
    )

    assert previous_tag == PREVIOUS_RELEASE["tag_name"]
    assert ("GET", f"/repos/{REPOSITORY}/releases?per_page=100&page=2", None) in (
        client.requests
    )


def test_identical_previous_release_tag_is_a_valid_empty_bound() -> None:
    client = delivery_client()
    client.responses[
        ("GET", f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100")
    ] = {"status": "identical"}

    previous_tag = ReleaseDelivery(client)._find_previous_release(
        REPOSITORY,
        str(RELEASE["tag_name"]),
        datetime.fromisoformat(str(RELEASE["published_at"]).replace("Z", "+00:00")),
    )

    assert previous_tag == PREVIOUS_RELEASE["tag_name"]


def test_first_github_release_uses_the_previous_version_tag() -> None:
    client = delivery_client()
    releases_path = f"/repos/{REPOSITORY}/releases?per_page=100&page=1"
    client.responses[("GET", releases_path)] = [RELEASE]
    client.responses[("GET", f"/repos/{REPOSITORY}/tags?per_page=100&page=1")] = [
        {"name": "v2.12.1100"},
        {"name": "v2.12.1099"},
        {"name": "not-a-version"},
    ]

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:1"
    assert (
        "GET",
        f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100?per_page=100&page=1",
        None,
    ) in client.requests


def test_previous_release_must_be_in_the_current_tag_ancestry() -> None:
    client = delivery_client()
    divergent_release = {
        "tag_name": "v3.0.0",
        "draft": False,
        "published_at": "2026-08-13T08:30:00Z",
    }
    releases_path = f"/repos/{REPOSITORY}/releases?per_page=100&page=1"
    client.responses[("GET", releases_path)] = [
        RELEASE,
        divergent_release,
        PREVIOUS_RELEASE,
    ]
    client.responses[("GET", f"/repos/{REPOSITORY}/compare/v3.0.0...v2.12.1100")] = {
        "status": "diverged"
    }

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:1"
    assert (
        "GET",
        f"/repos/{REPOSITORY}/compare/v3.0.0...v2.12.1100",
        None,
    ) in client.requests
    assert (
        "GET",
        f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100?per_page=100&page=1",
        None,
    ) in client.requests


def test_delivery_audit_preserves_every_linked_pull_request() -> None:
    client = delivery_client()
    issue_path = f"/repos/{REPOSITORY}/issues/42"
    compare_path = (
        f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100?per_page=100&page=1"
    )
    client.responses[("GET", compare_path)]["commits"].append(
        {
            "sha": "merge456",
            "commit": {"message": "fix(power): corrective fix\n\nRefs #42"},
        }
    )
    client.responses[("GET", compare_path)]["total_commits"] = 2
    second_pull_path = f"/repos/{REPOSITORY}/commits/merge456/pulls?per_page=100"
    client.responses[("GET", second_pull_path)] = [
        {
            "number": 91,
            "html_url": "https://github.com/Plaintext-Lab/PowerSync/pull/91",
            "body": "This body may be edited after merge.",
            "merged_at": "2026-08-13T08:30:00Z",
            "head": {"sha": "head456"},
        }
    ]
    client.responses[
        (
            "GET",
            f"/repos/{REPOSITORY}/commits/head456/check-runs?filter=latest&per_page=100&page=1",
        )
    ] = {
        "check_runs": [
            {"name": "tests", "status": "completed", "conclusion": "success"}
        ],
        "total_count": 1,
    }

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:1"
    posted_comment = next(
        payload
        for method, path, payload in client.requests
        if method == "POST" and path == f"{issue_path}/comments"
    )
    assert posted_comment is not None
    assert "PowerSync/pull/90" in posted_comment["body"]
    assert "PowerSync/pull/91" in posted_comment["body"]
