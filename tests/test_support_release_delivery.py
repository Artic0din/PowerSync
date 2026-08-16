from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest

from scripts.support_issue_state import SupportIssueAutomation
from scripts.support_release_delivery import (
    ReleaseDelivery,
    delivery_marker,
    delivery_pending_marker,
)


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
        if method == "POST" and path.endswith("/labels") and isinstance(payload, dict):
            issue_path = path[: -len("/labels")]
            issue = self.responses.get(("GET", issue_path))
            labels = payload.get("labels")
            if isinstance(issue, dict) and isinstance(labels, list):
                current = [
                    label
                    for label in issue.get("labels", [])
                    if isinstance(label, dict)
                ]
                names = {str(label.get("name", "")) for label in current}
                current.extend(
                    {"name": label} for label in labels if label not in names
                )
                issue["labels"] = current
        if method == "DELETE" and "/labels/" in path:
            issue_path, encoded_label = path.rsplit("/labels/", 1)
            issue = self.responses.get(("GET", issue_path))
            if isinstance(issue, dict):
                removed = unquote(encoded_label)
                issue["labels"] = [
                    label
                    for label in issue.get("labels", [])
                    if not isinstance(label, dict) or label.get("name") != removed
                ]
        return self.responses.get((method, path), {})


REPOSITORY = "Plaintext-Lab/PowerSync"
RELEASE = {
    "tag_name": "v2.12.1100",
    "html_url": "https://github.com/Plaintext-Lab/PowerSync/releases/tag/v2.12.1100",
    "draft": False,
    "prerelease": False,
    "published_at": "2026-08-13T09:00:00Z",
}
PREVIOUS_RELEASE = {
    "tag_name": "v2.12.1099",
    "draft": False,
    "prerelease": False,
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
            ): {"status": "ahead", "ahead_by": 1},
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
    pull_url = "https://github.com/Plaintext-Lab/PowerSync/pull/90"
    release_url = "https://github.com/Plaintext-Lab/PowerSync/releases/tag/v2.12.1100"
    marker = delivery_marker(pull_url, release_url)
    pending_marker = delivery_pending_marker(pull_url, release_url)
    stable_body = (
        f"{marker}\n{pending_marker}\n"
        f"Fix delivered in {pull_url} and released as {release_url}. "
        "Waiting for the reporter to confirm with `/powersync solved`."
    )

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
            {"body": stable_body},
        ),
    ]


def test_delivery_comment_keeps_cleanup_marker_after_final_state_check() -> None:
    client = delivery_client()
    pull_url = "https://github.com/Plaintext-Lab/PowerSync/pull/90"
    release_url = str(RELEASE["html_url"])
    cleanup_marker = delivery_pending_marker(pull_url, release_url)

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:1"
    posted = next(
        payload
        for method, path, payload in client.requests
        if method == "POST" and path.endswith("/comments")
    )
    assert cleanup_marker in str(posted["body"])
    assert client.requests[-1] == (
        "GET",
        f"/repos/{REPOSITORY}/issues/42",
        None,
    )


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


def test_prerelease_does_not_transition_reporter_issues() -> None:
    client = delivery_client()
    prerelease = {**RELEASE, "prerelease": True}

    with pytest.raises(ValueError, match="stable published release"):
        ReleaseDelivery(client).record(REPOSITORY, prerelease)

    assert client.requests == []


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


def test_open_rerun_finalizes_a_pending_delivery_comment() -> None:
    client = delivery_client()
    issue_path = f"/repos/{REPOSITORY}/issues/42"
    pull_url = "https://github.com/Plaintext-Lab/PowerSync/pull/90"
    release_url = str(RELEASE["html_url"])
    marker = delivery_marker(pull_url, release_url)
    pending_marker = delivery_pending_marker(pull_url, release_url)
    client.responses[("GET", issue_path)]["labels"] = [
        {"name": "awaiting confirmation"}
    ]
    client.responses[("GET", f"{issue_path}/comments?per_page=100&page=1")] = [
        {
            "id": 987,
            "body": f"{marker}\n{pending_marker}\nWaiting",
            "user": {"login": "github-actions[bot]"},
        }
    ]

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:0"
    assert (
        "PATCH",
        f"/repos/{REPOSITORY}/issues/comments/987",
        {
            "body": f"{marker}\n{pending_marker}\n"
            f"Fix delivered in {pull_url} and released as "
            f"{release_url}. Waiting for the reporter to confirm with "
            "`/powersync solved`."
        },
    ) in client.requests


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


def test_release_workflow_reconciles_only_after_valid_release_state() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    resolve_step = workflow.split("- name: Resolve published release tag", 1)[1].split(
        "\n      - name:", 1
    )[0]
    reconcile_step = workflow.split("- name: Reconcile released support fixes", 1)[
        1
    ].split("\n      - name:", 1)[0]

    assert "steps.check_release.outcome == 'success'" in resolve_step
    assert "steps.create_release.outcome == 'success'" in resolve_step
    assert "steps.check_release.outcome == 'success'" in reconcile_step
    assert "steps.create_release.outcome == 'success'" in reconcile_step


def test_release_workflow_rejects_drafts_as_existing_publications() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert ".draft == false" in workflow
    assert ".prerelease == false" in workflow
    assert '(.published_at | type) == "string"' in workflow


def test_published_release_lookup_fails_on_errors_other_than_not_found() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    resolve_step = workflow.split("- name: Resolve published release tag", 1)[1].split(
        "\n      - name:", 1
    )[0]

    assert "releases/tags/$candidate" in resolve_step
    assert "(HTTP 404)" in resolve_step
    assert 'cat "$error_file" >&2' in resolve_step
    assert "2>/dev/null || true" not in resolve_step


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


def test_existing_release_retries_notification_and_cleanup() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    for step_name in (
        "Build Discord payloads",
        "Notify Discord",
        "Clear release notes",
    ):
        step = workflow.split(f"- name: {step_name}", 1)[1].split("\n      - name:", 1)[0]
        assert "steps.support_release.outputs.tag != ''" in step
        assert "release_exists == 'false'" not in step
    assert 'tag == target_tag and Path("/tmp/release_notes.md").exists()' in workflow


def test_discord_receipt_is_persisted_per_release_before_later_cleanup() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    build = workflow.split("- name: Build Discord payloads", 1)[1].split(
        "\n      - name:", 1
    )[0]
    notify = workflow.split("- name: Notify Discord", 1)[1].split(
        "\n      - name:", 1
    )[0]
    cleanup = workflow.split("- name: Clear release notes", 1)[1].split(
        "\n      - name:", 1
    )[0]

    assert "contents/{marker_path}?ref=main" in build
    assert "record_discord_marker" in notify
    assert notify.index('record_discord_marker "$tag"') > notify.index("HTTP_CODE")
    assert "git fetch origin main" in cleanup
    assert "git checkout -B release-cleanup origin/main" in cleanup
    assert "discord-release-last-posted.txt" not in cleanup
    assert "group: release-main" in workflow
    assert "cancel-in-progress: false" in workflow


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
    assert "validate-automation" in workflow
    assert workflow.index("validate-automation") < workflow.index(
        "Enter the protected Graphite merge queue"
    )
    assert 'squash_merge_commit_message == "COMMIT_MESSAGES"' in workflow
    assert 'git log -1 --format=%B' in workflow
    assert workflow.index("Record immutable delivery evidence") < workflow.index(
        "Enter the protected Graphite merge queue"
    )
    assert (
        "types: [opened, reopened, labeled, unlabeled, ready_for_review, "
        "converted_to_draft, synchronize, closed]"
        in workflow
    )
    assert "github.event.pull_request.merged == false" in workflow
    assert "github.event.pull_request.head.repo.full_name == github.repository" in workflow
    assert "workflow_dispatch:" in workflow
    assert "Clear stale queue label before revalidation or after eligibility loss" in workflow
    assert "--remove-label merge-queue" in workflow
    assert workflow.index(
        "Clear stale queue label before revalidation or after eligibility loss"
    ) < workflow.index("Select the next automation pull request")
    assert "github.event.action == 'unlabeled'" in workflow
    assert "github.event.label.name == 'automation'" in workflow
    assert "github.event.action == 'converted_to_draft'" in workflow
    assert "github.event.action == 'synchronize'" in workflow
    assert "contains(github.event.pull_request.labels.*.name, 'merge-queue')" in workflow
    assert ".allow_merge_commit == false" in workflow
    assert ".allow_rebase_merge == false" in workflow


def test_automation_queue_reuses_reservation_and_revalidates_live_pr() -> None:
    workflow = Path(".github/workflows/queue-automated-fixes.yml").read_text(
        encoding="utf-8"
    )

    evidence_step = workflow.split("- name: Record immutable delivery evidence", 1)[
        1
    ].split("\n      - name:", 1)[0]
    queue_step = workflow.split("- name: Enter the protected Graphite merge queue", 1)[
        1
    ]

    assert "Reusing existing release reservation" in evidence_step
    assert "git diff --cached --quiet" in evidence_step
    assert 'head_sha=$(git rev-parse HEAD)' in evidence_step
    assert 'gh api "/repos/${GITHUB_REPOSITORY}/pulls/$PR_NUMBER"' in queue_step
    assert '.head.sha == $head_sha' in queue_step
    assert '.base.ref == "main"' in queue_step
    assert '.state == "open"' in queue_step
    assert '.draft == false' in queue_step
    assert 'map(.name) | index("automation")' in queue_step


def test_pull_request_validation_requires_immutable_support_references() -> None:
    workflow = Path(".github/workflows/validate.yml").read_text(encoding="utf-8")

    assert "Validate immutable support references" in workflow
    assert "validate-reference" in workflow
    assert 'squash_merge_commit_message == "COMMIT_MESSAGES"' in workflow
    assert ".allow_merge_commit == false" in workflow
    assert ".allow_rebase_merge == false" in workflow
    assert (
        "types: [opened, synchronize, reopened, ready_for_review, edited, labeled]"
        in workflow
    )


def test_solved_comment_filter_defers_whitespace_handling_to_the_parser() -> None:
    workflow = Path(".github/workflows/support-issue-state.yml").read_text(
        encoding="utf-8"
    )

    assert "github.event.issue.pull_request == null" in workflow
    assert "github.event.comment.body == '/powersync solved'" not in workflow


def test_queue_runs_after_release_workflow_completion() -> None:
    workflow = Path(".github/workflows/queue-automated-fixes.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_run:" in workflow
    assert 'workflows: ["Create Release on Version Bump"]' in workflow
    assert "types: [completed]" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow


def test_queue_requires_a_published_release_for_the_current_manifest_version() -> None:
    workflow = Path(".github/workflows/queue-automated-fixes.yml").read_text(
        encoding="utf-8"
    )

    gate = workflow.split("- name: Select the next automation pull request", 1)[1]
    assert "custom_components/power_sync/manifest.json" in gate
    assert "RELEASE_VERSION=$(jq -r '.version'" in gate
    assert 'gh release view "v$RELEASE_VERSION"' in gate
    assert ".isDraft == false and .isPrerelease == false" in gate
    assert '"v" + $version' in gate
    assert gate.index('gh release view "v$RELEASE_VERSION"') < gate.index(
        "prepare_automated_release.py select"
    )


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


def test_release_requires_delivery_gate_before_posting_audit() -> None:
    client = delivery_client()
    issue_path = f"/repos/{REPOSITORY}/issues/42"
    client.response_sequences[("GET", issue_path)] = [
        {
            "number": 42,
            "state": "open",
            "labels": [{"name": "needs investigation"}],
        },
        {"number": 42, "state": "open", "labels": []},
    ]

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:0"
    assert not any(
        method == "POST" and path.endswith("/comments")
        for method, path, _ in client.requests
    )


def test_delivery_comment_is_rolled_back_when_gate_is_removed() -> None:
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
        {"number": 42, "state": "open", "labels": []},
    ]

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:0"
    assert (
        "DELETE",
        f"/repos/{REPOSITORY}/issues/comments/987",
        None,
    ) in client.requests


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


def test_terminal_rerun_retries_pending_delivery_comment_cleanup() -> None:
    client = delivery_client()
    issue_path = f"/repos/{REPOSITORY}/issues/42"
    pull_url = "https://github.com/Plaintext-Lab/PowerSync/pull/90"
    release_url = str(RELEASE["html_url"])
    client.responses[("GET", issue_path)] = {
        "number": 42,
        "state": "closed",
        "labels": [{"name": "solved"}, {"name": "awaiting confirmation"}],
    }
    client.responses[("GET", f"{issue_path}/comments?per_page=100&page=1")] = [
        {
            "id": 987,
            "body": f"{delivery_marker(pull_url, release_url)}\n"
            f"{delivery_pending_marker(pull_url, release_url)}\nWaiting",
            "user": {"login": "github-actions[bot]"},
        }
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


def test_closed_unsolved_rerun_retries_pending_delivery_comment_cleanup() -> None:
    client = delivery_client()
    issue_path = f"/repos/{REPOSITORY}/issues/42"
    pull_url = "https://github.com/Plaintext-Lab/PowerSync/pull/90"
    release_url = str(RELEASE["html_url"])
    client.responses[("GET", issue_path)] = {
        "number": 42,
        "state": "closed",
        "labels": [],
    }
    client.responses[("GET", f"{issue_path}/comments?per_page=100&page=1")] = [
        {
            "id": 987,
            "body": f"{delivery_marker(pull_url, release_url)}\n"
            f"{delivery_pending_marker(pull_url, release_url)}\nWaiting",
            "user": {"login": "github-actions[bot]"},
        }
    ]

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:0"
    assert (
        "DELETE",
        f"/repos/{REPOSITORY}/issues/comments/987",
        None,
    ) in client.requests


def test_previous_release_search_scans_every_bounded_page() -> None:
    client = FakeGitHubClient()
    first_page = [
        {
            "tag_name": "v2.12.1098",
            "draft": False,
            "prerelease": False,
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
    ] = {"status": "ahead", "ahead_by": 1}

    previous_tag = ReleaseDelivery(client)._find_previous_release(
        REPOSITORY,
        str(RELEASE["tag_name"]),
        datetime.fromisoformat(str(RELEASE["published_at"]).replace("Z", "+00:00")),
    )

    assert previous_tag == PREVIOUS_RELEASE["tag_name"]
    assert ("GET", f"/repos/{REPOSITORY}/releases?per_page=100&page=2", None) in (
        client.requests
    )


def test_previous_release_search_uses_bounded_candidate_before_history_limit() -> None:
    client = FakeGitHubClient()
    for page in range(1, 11):
        releases = [{"draft": True} for _ in range(100)]
        if page == 1:
            releases[0] = PREVIOUS_RELEASE
        client.responses[
            ("GET", f"/repos/{REPOSITORY}/releases?per_page=100&page={page}")
        ] = releases
    client.responses[
        ("GET", f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100")
    ] = {"status": "ahead", "ahead_by": 1}

    previous_tag = ReleaseDelivery(client)._find_previous_release(
        REPOSITORY,
        str(RELEASE["tag_name"]),
        datetime.fromisoformat(str(RELEASE["published_at"]).replace("Z", "+00:00")),
    )

    assert previous_tag == PREVIOUS_RELEASE["tag_name"]


def test_previous_release_search_limits_ancestry_requests() -> None:
    client = FakeGitHubClient()
    releases = [
        {
            "tag_name": f"v2.12.{1099 - index}",
            "draft": False,
            "prerelease": False,
            "published_at": f"2026-08-12T{23 - index // 3:02d}:{59 - index % 3:02d}:00Z",
        }
        for index in range(51)
    ]
    client.responses[("GET", f"/repos/{REPOSITORY}/releases?per_page=100&page=1")] = (
        releases
    )

    with pytest.raises(ValueError, match="supported search bound"):
        ReleaseDelivery(client)._find_previous_release(
            REPOSITORY,
            str(RELEASE["tag_name"]),
            datetime.fromisoformat(
                str(RELEASE["published_at"]).replace("Z", "+00:00")
            ),
        )

    ancestry_requests = [
        path for method, path, _ in client.requests if method == "GET" and "/compare/" in path
    ]
    assert len(ancestry_requests) == 50


def test_previous_version_search_uses_bounded_candidate_before_tag_limit() -> None:
    client = FakeGitHubClient()
    for page in range(1, 11):
        tags = [{"name": "not-a-version"} for _ in range(100)]
        if page == 1:
            tags[0] = {"name": "v2.12.1099"}
        client.responses[
            ("GET", f"/repos/{REPOSITORY}/tags?per_page=100&page={page}")
        ] = tags
    client.responses[
        ("GET", f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100")
    ] = {"status": "ahead", "ahead_by": 1}

    previous_tag = ReleaseDelivery(client)._find_previous_version_tag(
        REPOSITORY, "v2.12.1100"
    )

    assert previous_tag == "v2.12.1099"


def test_refs_in_commit_prose_or_fenced_examples_are_not_delivery_metadata() -> None:
    for message in (
        "docs: explain support syntax\n\nFor example, use Refs #42 in a commit.",
        "docs: explain support syntax\n\n```text\nRefs #42\n```",
    ):
        client = delivery_client()
        compare_path = (
            f"/repos/{REPOSITORY}/compare/"
            "v2.12.1099...v2.12.1100?per_page=100&page=1"
        )
        client.responses[("GET", compare_path)]["commits"][0]["commit"][
            "message"
        ] = message

        result = SupportIssueAutomation(client).handle(release_event())

        assert result == "deliveries-recorded:0"
        assert all(method == "GET" for method, _, _ in client.requests)


def test_identical_previous_release_tag_is_a_valid_empty_bound() -> None:
    client = delivery_client()
    client.responses[
        ("GET", f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100")
    ] = {"status": "identical", "ahead_by": 0}

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
        "prerelease": False,
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


def test_previous_release_is_nearest_ancestor_not_latest_publication() -> None:
    client = delivery_client()
    backfilled_release = {
        "tag_name": "v2.12.1098",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-08-13T08:30:00Z",
    }
    releases_path = f"/repos/{REPOSITORY}/releases?per_page=100&page=1"
    client.responses[("GET", releases_path)] = [
        RELEASE,
        backfilled_release,
        PREVIOUS_RELEASE,
    ]
    client.responses[
        ("GET", f"/repos/{REPOSITORY}/compare/v2.12.1098...v2.12.1100")
    ] = {"status": "ahead", "ahead_by": 2}
    client.responses[
        ("GET", f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100")
    ] = {"status": "ahead", "ahead_by": 1}

    previous_tag = ReleaseDelivery(client)._find_previous_release(
        REPOSITORY,
        str(RELEASE["tag_name"]),
        datetime.fromisoformat(str(RELEASE["published_at"]).replace("Z", "+00:00")),
    )

    assert previous_tag == PREVIOUS_RELEASE["tag_name"]


def test_previous_release_search_skips_a_deleted_candidate_tag() -> None:
    client = delivery_client()
    orphaned_release = {
        "tag_name": "v2.12.1099-hotfix",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-08-12T12:00:00Z",
    }
    releases_path = f"/repos/{REPOSITORY}/releases?per_page=100&page=1"
    client.responses[("GET", releases_path)] = [
        RELEASE,
        orphaned_release,
        PREVIOUS_RELEASE,
    ]
    orphan_compare = (
        f"/repos/{REPOSITORY}/compare/v2.12.1099-hotfix...v2.12.1100"
    )
    client.responses[("GET", orphan_compare)] = {}

    result = SupportIssueAutomation(client).handle(release_event())

    assert result == "deliveries-recorded:1"
    assert ("GET", orphan_compare, None) in client.requests
    assert (
        "GET",
        f"/repos/{REPOSITORY}/compare/v2.12.1099...v2.12.1100",
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
