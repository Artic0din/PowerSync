"""Deterministic confirmation controls for GitHub support issues."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Protocol
from urllib.parse import quote

from scripts.support_release_delivery import WORKFLOW_BOT_LOGIN, ReleaseDelivery

Action = Literal["solved"]
WRITE_PERMISSIONS = frozenset({"admin", "maintain", "write"})
POWER_SYNC_REPOSITORIES = frozenset({"plaintext-lab/powersync", "bolagnaise/powersync"})
RESOLUTION_MARKER_PREFIX = "powersync-resolution:v1:"
MAX_API_PAGES = 10


class GitHubApi(Protocol):
    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class Command:
    action: Action


def parse_command(body: str) -> Command | None:
    """Return a command only when the complete comment matches the contract."""
    return Command(action="solved") if body.strip() == "/powersync solved" else None


def resolution_marker(comment_id: int) -> str:
    """Bind the audit marker to one immutable confirmation event."""
    return f"<!-- {RESOLUTION_MARKER_PREFIX}{comment_id} -->"


class SupportIssueAutomation:
    """Apply published-release delivery and explicit confirmation transitions."""

    def __init__(
        self,
        client: GitHubApi,
        allowed_repositories: Iterable[str] = POWER_SYNC_REPOSITORIES,
    ) -> None:
        self._client = client
        self._release_delivery = ReleaseDelivery(client)
        self._allowed_repositories = {
            repository.casefold() for repository in allowed_repositories
        }

    def handle(self, event: dict[str, Any]) -> str:
        repository = event.get("repository", {}).get("full_name", "")
        if not repository:
            raise ValueError("The GitHub event is missing repository metadata")
        self._require_allowed_repository(repository)

        if event.get("action") == "published" and isinstance(
            event.get("release"), dict
        ):
            return self._release_delivery.record(repository, event["release"])
        release_tag = event.get("inputs", {}).get("release_tag")
        if isinstance(release_tag, str) and release_tag:
            release = self._client.request(
                "GET",
                f"/repos/{repository}/releases/tags/{quote(release_tag, safe='')}",
            )
            if not isinstance(release, dict):
                raise ValueError("GitHub returned invalid release metadata")
            return self._release_delivery.record(repository, release)
        return self._handle_confirmation(repository, event)

    def _handle_confirmation(self, repository: str, event: dict[str, Any]) -> str:
        issue_event = event.get("issue", {})
        if event.get("action") != "created" or issue_event.get("pull_request"):
            return "ignored"
        if parse_command(event.get("comment", {}).get("body", "")) is None:
            return "ignored"

        actor = event.get("comment", {}).get("user", {}).get("login", "")
        comment_id = event.get("comment", {}).get("id")
        issue_number = issue_event.get("number")
        if (
            not actor
            or not isinstance(comment_id, int)
            or not isinstance(issue_number, int)
        ):
            raise ValueError("The GitHub event is missing required issue metadata")

        issue_path = f"/repos/{repository}/issues/{issue_number}"
        issue = self._client.request("GET", issue_path)
        if not isinstance(issue, dict):
            raise ValueError("GitHub returned invalid issue state")
        author = issue.get("user", {}).get("login", "")
        labels = self._label_names(issue)
        if not author:
            raise ValueError("The GitHub issue has no author")
        has_delivery_gate = "awaiting confirmation" in labels
        is_terminal_retry = "solved" in labels and issue.get("state") == "closed"
        if not has_delivery_gate and not is_terminal_retry:
            return "invalid-state"
        if actor.casefold() != author.casefold() and not self._has_write_access(
            repository, actor
        ):
            return "unauthorized"

        marker = resolution_marker(comment_id)
        marker_exists = self._has_comment_marker(issue_path, marker)
        if "solved" not in labels:
            self._request("POST", f"{issue_path}/labels", {"labels": ["solved"]})
        if issue.get("state") != "closed":
            self._request(
                "PATCH",
                issue_path,
                {"state": "closed", "state_reason": "completed"},
            )
        if not marker_exists:
            self._request(
                "POST",
                f"{issue_path}/comments",
                {
                    "body": f"{marker}\nResolution confirmed by @{actor} "
                    "using `/powersync solved`. Closing this issue as completed."
                },
            )
        self._request(
            "DELETE", f"{issue_path}/labels/{quote('awaiting confirmation', safe='')}"
        )
        return "closed-confirmed"

    def _has_write_access(self, repository: str, actor: str) -> bool:
        response = self._request(
            "GET",
            f"/repos/{repository}/collaborators/{quote(actor, safe='')}/permission",
        )
        return (
            isinstance(response, dict)
            and response.get("permission") in WRITE_PERMISSIONS
        )

    def _has_comment_marker(self, issue_path: str, marker: str) -> bool:
        for page in range(1, MAX_API_PAGES + 1):
            comments = self._request(
                "GET", f"{issue_path}/comments?per_page=100&page={page}"
            )
            if not isinstance(comments, list):
                raise ValueError("GitHub returned invalid issue comments")
            if any(
                marker in str(comment.get("body", ""))
                and self._comment_author(comment) == WORKFLOW_BOT_LOGIN
                for comment in comments
                if isinstance(comment, dict)
            ):
                return True
            if len(comments) < 100:
                return False
        raise ValueError("Issue comments exceed the supported pagination limit")

    @staticmethod
    def _comment_author(comment: dict[str, Any]) -> str:
        user = comment.get("user")
        if isinstance(user, dict) and isinstance(user.get("login"), str):
            return user["login"]
        return ""

    @staticmethod
    def _label_names(issue: dict[str, Any]) -> set[str]:
        return {
            label.get("name", "")
            for label in issue.get("labels", [])
            if isinstance(label, dict)
        }

    def _require_allowed_repository(self, repository: str) -> None:
        if repository.casefold() not in self._allowed_repositories:
            raise ValueError(
                f"Repository is not allowed for support state: {repository}"
            )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        return self._client.request(method, path, payload)
