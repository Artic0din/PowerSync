"""GitHub Actions entry point for the support issue state machine."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scripts.support_issue_state import SupportIssueAutomation


class GitHubClient:
    """Minimal GitHub REST client using the workflow's short-lived token."""

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("GITHUB_TOKEN is required")
        self._token = token

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            f"https://api.github.com{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                content = response.read()
        except HTTPError as error:
            missing_label = method == "DELETE" and "/labels/" in path
            missing_permission = method == "GET" and path.endswith("/permission")
            missing_issue = (
                method == "GET"
                and re.fullmatch(r"/repos/[^/]+/[^/]+/issues/\d+", path) is not None
            )
            if error.code == 404 and (
                missing_label or missing_permission or missing_issue
            ):
                return {}
            detail = error.read().decode(errors="replace")
            raise RuntimeError(
                f"GitHub API {method} {path} failed: {detail}"
            ) from error
        except URLError as error:
            raise RuntimeError(
                f"GitHub API {method} {path} failed: {error.reason}"
            ) from error
        return json.loads(content) if content else {}


def main() -> int:
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        raise ValueError("GITHUB_EVENT_PATH is required")
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    automation = SupportIssueAutomation(
        GitHubClient(os.environ.get("GITHUB_TOKEN", ""))
    )
    result = automation.handle(event)
    print(f"PowerSync support issue transition: {result}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"::error title=Support issue command rejected::{error}", file=sys.stderr)
        raise SystemExit(1) from error
