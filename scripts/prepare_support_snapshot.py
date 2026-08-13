"""Capture a bounded immutable issue-evidence snapshot before agent execution."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from scripts.run_support_intake import GitHubClient
from scripts.support_intake import IntakeSnapshot, SupportIntake

SNAPSHOT_NAME = ".powersync-support-evidence.md"


def persist_snapshot(
    snapshot: IntakeSnapshot, safe_outputs_path: Path, workspace: Path
) -> bool:
    evidence_is_currently_safe = (
        snapshot.decision.safe
        and "safe evidence" in snapshot.labels
        and "unsafe evidence" not in snapshot.labels
    )
    if not evidence_is_currently_safe:
        with safe_outputs_path.open("a", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    {
                        "type": "noop",
                        "message": "Current issue evidence did not pass the deterministic gate.",
                    }
                )
                + "\n"
            )
        return False

    snapshot_path = workspace / SNAPSHOT_NAME
    snapshot_path.write_text(snapshot.content, encoding="utf-8")
    snapshot_path.chmod(0o600)
    exclude_path = workspace / ".git" / "info" / "exclude"
    with exclude_path.open("a", encoding="utf-8") as exclude:
        exclude.write(f"\n/{SNAPSHOT_NAME}\n")
    return True


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    issue_number_text = os.environ.get("SUPPORT_ISSUE_NUMBER", "")
    safe_outputs_path = os.environ.get("GH_AW_SAFE_OUTPUTS", "")
    if not repository or not issue_number_text.isdigit() or not safe_outputs_path:
        raise ValueError("The workflow is missing support snapshot metadata")

    snapshot = SupportIntake(GitHubClient(os.environ.get("GITHUB_TOKEN", ""))).evaluate(
        repository, int(issue_number_text)
    )
    persist_snapshot(snapshot, Path(safe_outputs_path), Path.cwd())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"::error title=Support snapshot failed::{error}", file=sys.stderr)
        raise SystemExit(1) from error
