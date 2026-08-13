"""GitHub Actions entry point for deterministic support evidence intake."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scripts.support_intake import SupportIntake


class GitHubClient:
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
            if error.code == 404 and method == "DELETE" and "/labels/" in path:
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

    @staticmethod
    def download(url: str, maximum_bytes: int) -> bytes:
        request = Request(url, headers={"User-Agent": "PowerSync-support-intake/1"})
        try:
            with urlopen(request, timeout=30) as response:
                content = response.read(maximum_bytes + 1)
        except (HTTPError, URLError) as error:
            raise ValueError("attachment could not be downloaded") from error
        if len(content) > maximum_bytes:
            raise ValueError("attachment exceeds the support evidence size limit")
        return content


def main() -> int:
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        raise ValueError("GITHUB_EVENT_PATH is required")
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    repository = event.get("repository", {}).get("full_name", "")
    issue_number = event.get("issue", {}).get("number")
    if not repository or not isinstance(issue_number, int):
        raise ValueError("The GitHub event is missing issue metadata")
    decision = SupportIntake(GitHubClient(os.environ.get("GITHUB_TOKEN", ""))).inspect(
        repository, issue_number
    )
    print(
        f"PowerSync support intake safe={decision.safe} reasons={len(decision.reasons)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"::error title=Support intake failed::{error}", file=sys.stderr)
        raise SystemExit(1) from error
