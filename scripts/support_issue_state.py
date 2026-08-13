"""Deterministic delivery and closure controls for GitHub support issues."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Literal, Protocol
from urllib.parse import quote, unquote, urlparse

Action = Literal["delivered", "solved"]
WRITE_PERMISSIONS = frozenset({"admin", "maintain", "write"})
SUCCESSFUL_CHECK_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
POWER_SYNC_REPOSITORIES = frozenset({"plaintext-lab/powersync", "bolagnaise/powersync"})
DELIVERED_PATTERN = re.compile(
    r"\A/powersync delivered (https://github\.com/[^\s]+/[^\s]+/pull/\d+) "
    r"(https://github\.com/[^\s]+/[^\s]+/releases/tag/[^\s]+)\Z"
)


class GitHubApi(Protocol):
    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Command:
    action: Action
    pull_request_url: str | None = None
    release_url: str | None = None


def parse_command(body: str) -> Command | None:
    """Return a command only when the complete comment matches the contract."""
    normalized = body.strip()
    if normalized == "/powersync solved":
        return Command(action="solved")
    match = DELIVERED_PATTERN.fullmatch(normalized)
    if match is None:
        return None
    return Command(
        action="delivered",
        pull_request_url=match.group(1),
        release_url=match.group(2),
    )


class SupportIssueAutomation:
    """Apply authorised support-issue state transitions through the GitHub API."""

    def __init__(
        self,
        client: GitHubApi,
        allowed_repositories: Iterable[str] = POWER_SYNC_REPOSITORIES,
    ) -> None:
        self._client = client
        self._allowed_repositories = {
            repository.casefold() for repository in allowed_repositories
        }

    def handle(self, event: dict[str, Any]) -> str:
        issue = event.get("issue", {})
        if event.get("action") != "created" or "pull_request" in issue:
            return "ignored"

        command = parse_command(event.get("comment", {}).get("body", ""))
        if command is None:
            return "ignored"
        if issue.get("state") != "open":
            return "invalid-state"

        repository = event.get("repository", {}).get("full_name", "")
        actor = event.get("comment", {}).get("user", {}).get("login", "")
        author = issue.get("user", {}).get("login", "")
        issue_number = issue.get("number")
        if (
            not repository
            or not actor
            or not author
            or not isinstance(issue_number, int)
        ):
            raise ValueError("The GitHub event is missing required issue metadata")

        labels = {
            label.get("name", "")
            for label in issue.get("labels", [])
            if isinstance(label, dict)
        }
        if command.action == "solved":
            return self._confirm_resolution(
                repository, issue_number, actor, author, labels
            )
        return self._record_delivery(repository, issue_number, actor, labels, command)

    def _confirm_resolution(
        self,
        repository: str,
        issue_number: int,
        actor: str,
        author: str,
        labels: set[str],
    ) -> str:
        if "awaiting confirmation" not in labels:
            return "invalid-state"
        if actor.casefold() != author.casefold() and not self._has_write_access(
            repository, actor
        ):
            return "unauthorized"

        self._add_labels(repository, issue_number, ["solved"])
        self._remove_label(repository, issue_number, "awaiting confirmation")
        self._add_comment(
            repository,
            issue_number,
            f"Resolution confirmed by @{actor} using `/powersync solved`. "
            "Closing this issue as completed.",
        )
        self._client.request(
            "PATCH",
            f"/repos/{repository}/issues/{issue_number}",
            {"state": "closed", "state_reason": "completed"},
        )
        return "closed-confirmed"

    def _record_delivery(
        self,
        repository: str,
        issue_number: int,
        actor: str,
        labels: set[str],
        command: Command,
    ) -> str:
        if "awaiting confirmation" in labels:
            return "already-awaiting-confirmation"
        if not self._has_write_access(repository, actor):
            return "unauthorized"
        if command.pull_request_url is None or command.release_url is None:
            raise ValueError(
                "The delivered command requires pull request and release URLs"
            )

        pull_repository, pull_number = self._parse_pull_request_url(
            command.pull_request_url
        )
        release_repository, release_tag = self._parse_release_url(command.release_url)
        self._require_allowed_repository(pull_repository)
        self._require_allowed_repository(release_repository)

        pull_request = self._client.request(
            "GET", f"/repos/{pull_repository}/pulls/{pull_number}"
        )
        merged_at = self._parse_timestamp(pull_request.get("merged_at"), "merge")
        head_sha = pull_request.get("head", {}).get("sha")
        if not isinstance(head_sha, str) or not head_sha:
            raise ValueError("The merged pull request has no head commit")
        self._require_successful_checks(pull_repository, head_sha)

        encoded_tag = quote(release_tag, safe="")
        release = self._client.request(
            "GET", f"/repos/{release_repository}/releases/tags/{encoded_tag}"
        )
        if release.get("draft") is not False:
            raise ValueError("The supplied release must be published and not a draft")
        published_at = self._parse_timestamp(
            release.get("published_at"), "release publication"
        )
        if published_at < merged_at:
            raise ValueError(
                "The release must be published after the pull request was merged"
            )

        self._add_labels(repository, issue_number, ["awaiting confirmation"])
        for label in ("agent ready", "needs information", "needs investigation"):
            if label in labels:
                self._remove_label(repository, issue_number, label)
        self._add_comment(
            repository,
            issue_number,
            f"Delivery recorded by @{actor}. Fix: {command.pull_request_url}. "
            f"Release: {command.release_url}. Waiting for the reporter or a "
            "maintainer to confirm with `/powersync solved`.",
        )
        return "awaiting-confirmation"

    def _require_successful_checks(self, repository: str, head_sha: str) -> None:
        response = self._client.request(
            "GET", f"/repos/{repository}/commits/{head_sha}/check-runs?filter=latest"
        )
        checks = response.get("check_runs", [])
        successful = [
            check
            for check in checks
            if check.get("status") == "completed"
            and check.get("conclusion") in SUCCESSFUL_CHECK_CONCLUSIONS
        ]
        if not checks or len(successful) != len(checks):
            raise ValueError("The pull request must have a successful check run")
        if not any(check.get("conclusion") == "success" for check in successful):
            raise ValueError("The pull request must have a successful check run")

    def _has_write_access(self, repository: str, actor: str) -> bool:
        encoded_actor = quote(actor, safe="")
        response = self._client.request(
            "GET", f"/repos/{repository}/collaborators/{encoded_actor}/permission"
        )
        return response.get("permission") in WRITE_PERMISSIONS

    def _require_allowed_repository(self, repository: str) -> None:
        if repository.casefold() not in self._allowed_repositories:
            raise ValueError(
                f"Repository is not allowed for support evidence: {repository}"
            )

    @staticmethod
    def _parse_pull_request_url(url: str) -> tuple[str, int]:
        parts = SupportIssueAutomation._github_path_parts(url)
        if len(parts) != 4 or parts[2] != "pull" or not parts[3].isdigit():
            raise ValueError("The pull request URL is invalid")
        return f"{parts[0]}/{parts[1]}", int(parts[3])

    @staticmethod
    def _parse_release_url(url: str) -> tuple[str, str]:
        parts = SupportIssueAutomation._github_path_parts(url)
        if len(parts) < 5 or parts[2:4] != ["releases", "tag"]:
            raise ValueError("The release URL is invalid")
        return f"{parts[0]}/{parts[1]}", unquote("/".join(parts[4:]))

    @staticmethod
    def _github_path_parts(url: str) -> list[str]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "github.com":
            raise ValueError("Support evidence must use a github.com HTTPS URL")
        return [part for part in parsed.path.split("/") if part]

    @staticmethod
    def _parse_timestamp(value: Any, field: str) -> datetime:
        if not isinstance(value, str) or not value:
            raise ValueError(f"The supplied evidence has no {field} timestamp")
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _add_labels(
        self, repository: str, issue_number: int, labels: list[str]
    ) -> None:
        self._client.request(
            "POST",
            f"/repos/{repository}/issues/{issue_number}/labels",
            {"labels": labels},
        )

    def _remove_label(self, repository: str, issue_number: int, label: str) -> None:
        self._client.request(
            "DELETE",
            f"/repos/{repository}/issues/{issue_number}/labels/{quote(label, safe='')}",
        )

    def _add_comment(self, repository: str, issue_number: int, body: str) -> None:
        self._client.request(
            "POST",
            f"/repos/{repository}/issues/{issue_number}/comments",
            {"body": body},
        )
