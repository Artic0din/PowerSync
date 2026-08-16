"""Fail closed when accepted support evidence changes during agent execution."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Protocol

from scripts.run_support_intake import GitHubClient
from scripts.support_intake import (
    SAFETY_LABELS,
    SupportIntake,
    same_evidence_revision,
    snapshot_revision,
    snapshot_revision_labels,
)

ROUTE_LABEL_STATES = {
    "issue-investigation": (
        frozenset({"bug", "needs investigation"}),
        frozenset(
            {
                "enhancement",
                "question",
                "duplicate",
                "off topic",
                "spam",
                "needs triage",
                "needs information",
                "feature assessed",
            }
        ),
    ),
    "feature-assessment": (
        frozenset({"enhancement"}),
        frozenset(
            {
                "bug",
                "question",
                "duplicate",
                "off topic",
                "spam",
                "needs triage",
                "needs information",
                "needs investigation",
                "feature assessed",
            }
        ),
    ),
}
ROUTE_LABELS = frozenset(
    label
    for required_labels, forbidden_labels in ROUTE_LABEL_STATES.values()
    for label in required_labels | forbidden_labels
)


class RevalidationClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any: ...

    def download(self, url: str, maximum_bytes: int) -> bytes: ...


def revalidate_snapshot(
    client: RevalidationClient,
    repository: str,
    issue_number: int,
    expected_revision: str,
) -> bool:
    snapshot = SupportIntake(client).evaluate(repository, issue_number)
    return (
        snapshot.decision.safe
        and "safe evidence" in snapshot.labels
        and "unsafe evidence" not in snapshot.labels
        and snapshot_revision(snapshot) == expected_revision
    )


def refresh_snapshot_revision(
    client: RevalidationClient,
    repository: str,
    issue_number: int,
    expected_revision: str,
    expected_route: str,
) -> str | None:
    """Return a revision only for the route's exact classification state."""
    snapshot = SupportIntake(client).evaluate(repository, issue_number)
    current_revision = snapshot_revision(snapshot)
    original_labels = snapshot_revision_labels(expected_revision)
    route_state = ROUTE_LABEL_STATES.get(expected_route)
    if route_state is None or original_labels is None:
        return None
    required_labels, forbidden_labels = route_state
    current_labels = snapshot.labels - SAFETY_LABELS
    if (
        snapshot.decision.safe
        and "safe evidence" in snapshot.labels
        and "unsafe evidence" not in snapshot.labels
        and same_evidence_revision(current_revision, expected_revision)
        and required_labels <= snapshot.labels
        and snapshot.labels.isdisjoint(forbidden_labels)
        and current_labels - ROUTE_LABELS == original_labels - ROUTE_LABELS
    ):
        return current_revision
    return None


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    issue_number_text = os.environ.get("SUPPORT_ISSUE_NUMBER", "")
    expected_revision = os.environ.get("SUPPORT_EVIDENCE_REVISION", "")
    if not repository or not issue_number_text.isdigit() or not expected_revision:
        raise ValueError("The workflow is missing support revalidation metadata")
    client = GitHubClient(os.environ.get("GITHUB_TOKEN", ""))
    if os.environ.get("SUPPORT_REFRESH_REVISION") == "true":
        expected_route = os.environ.get("SUPPORT_EXPECTED_ROUTE", "")
        refreshed_revision = refresh_snapshot_revision(
            client,
            repository,
            int(issue_number_text),
            expected_revision,
            expected_route,
        )
        output_path = os.environ.get("GITHUB_OUTPUT", "")
        if refreshed_revision is None or not output_path:
            raise ValueError(
                "Issue evidence changed or became unsafe before workflow routing"
            )
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(f"evidence_revision={refreshed_revision}\n")
    elif not revalidate_snapshot(
        client, repository, int(issue_number_text), expected_revision
    ):
        raise ValueError("Issue evidence changed or became unsafe during agent execution")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"::error title=Support revalidation failed::{error}", file=sys.stderr)
        raise SystemExit(1) from error
