"""Fail closed when accepted support evidence changes during agent execution."""

from __future__ import annotations

import os
import sys
from typing import Any, Protocol

from scripts.run_support_intake import GitHubClient
from scripts.support_intake import SupportIntake, snapshot_revision


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


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    issue_number_text = os.environ.get("SUPPORT_ISSUE_NUMBER", "")
    expected_revision = os.environ.get("SUPPORT_EVIDENCE_REVISION", "")
    if not repository or not issue_number_text.isdigit() or not expected_revision:
        raise ValueError("The workflow is missing support revalidation metadata")
    if not revalidate_snapshot(
        GitHubClient(os.environ.get("GITHUB_TOKEN", "")),
        repository,
        int(issue_number_text),
        expected_revision,
    ):
        raise ValueError(
            "Issue evidence changed or became unsafe during agent execution"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"::error title=Support revalidation failed::{error}", file=sys.stderr)
        raise SystemExit(1) from error
