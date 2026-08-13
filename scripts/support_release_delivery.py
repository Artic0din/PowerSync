"""Map published releases to verified support-issue deliveries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import quote

SUCCESSFUL_CHECK_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
DELIVERY_MARKER_PREFIX = "powersync-delivery:v1:"
MAX_API_PAGES = 10
WORKFLOW_BOT_LOGIN = "github-actions[bot]"
VERSION_TAG_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class ReleasedPullRequest:
    metadata: dict[str, Any]
    commit_messages: tuple[str, ...]


def delivery_marker(pull_urls: str | tuple[str, ...], release_url: str) -> str:
    normalized_urls = (pull_urls,) if isinstance(pull_urls, str) else pull_urls
    identity_input = "\0".join((*normalized_urls, release_url))
    identity = sha256(identity_input.encode()).hexdigest()[:16]
    return f"<!-- {DELIVERY_MARKER_PREFIX}{identity} -->"


class GitHubApi(Protocol):
    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any: ...


class ReleaseDelivery:
    def __init__(self, client: GitHubApi) -> None:
        self._client = client

    def record(self, repository: str, release: dict[str, Any]) -> str:
        if release.get("draft") is not False:
            raise ValueError("The supplied release must be published and not a draft")
        current_tag = release.get("tag_name")
        release_url = release.get("html_url")
        published_at = self._parse_timestamp(
            release.get("published_at"), "release publication"
        )
        if not isinstance(current_tag, str) or not current_tag:
            raise ValueError("The release has no tag")
        if not isinstance(release_url, str) or not release_url:
            raise ValueError("The release has no URL")

        previous_tag = self._find_previous_release(
            repository, current_tag, published_at
        )
        if previous_tag is None:
            previous_tag = self._find_previous_version_tag(repository, current_tag)
        if previous_tag is None:
            raise ValueError("No earlier release or version tag can bound this release")
        pull_requests = self._pull_requests_between(
            repository, previous_tag, current_tag
        )

        deliveries: dict[int, list[str]] = {}
        for released_pull_request in pull_requests:
            pull_request = released_pull_request.metadata
            issue_numbers = set().union(
                *(
                    self._referenced_issues(repository, message)
                    for message in released_pull_request.commit_messages
                )
            )
            if not issue_numbers:
                continue
            merged_at = self._parse_timestamp(pull_request.get("merged_at"), "merge")
            if published_at <= merged_at:
                raise ValueError(
                    "The release must be published after the pull request was merged"
                )
            head_sha = pull_request.get("head", {}).get("sha")
            if not isinstance(head_sha, str) or not head_sha:
                raise ValueError("The merged pull request has no head commit")
            self._require_successful_checks(repository, head_sha)
            pull_url = pull_request.get("html_url")
            if not isinstance(pull_url, str) or not pull_url:
                raise ValueError("The merged pull request has no URL")
            for issue_number in issue_numbers:
                issue_pull_urls = deliveries.setdefault(issue_number, [])
                if pull_url not in issue_pull_urls:
                    issue_pull_urls.append(pull_url)

        recorded = sum(
            self._record_issue(repository, issue_number, tuple(pull_urls), release_url)
            for issue_number, pull_urls in deliveries.items()
        )
        return f"deliveries-recorded:{recorded}"

    def _find_previous_release(
        self, repository: str, current_tag: str, published_at: datetime
    ) -> str | None:
        candidates: list[tuple[datetime, str]] = []
        for page in range(1, MAX_API_PAGES + 1):
            releases = self._request(
                "GET", f"/repos/{repository}/releases?per_page=100&page={page}"
            )
            if not isinstance(releases, list):
                raise ValueError("GitHub returned invalid release history")
            for release in releases:
                if not isinstance(release, dict) or release.get("draft") is not False:
                    continue
                tag = release.get("tag_name")
                timestamp = release.get("published_at")
                if tag == current_tag or not isinstance(tag, str) or not timestamp:
                    continue
                candidate_time = self._parse_timestamp(timestamp, "release publication")
                if candidate_time < published_at:
                    candidates.append((candidate_time, tag))
            if len(releases) < 100:
                break
        else:
            raise ValueError("Release history exceeds the supported pagination limit")
        for _candidate_time, tag in sorted(candidates, reverse=True):
            if self._is_ancestor_tag(repository, tag, current_tag):
                return tag
        return None

    def _find_previous_version_tag(
        self, repository: str, current_tag: str
    ) -> str | None:
        current_version = self._version_tuple(current_tag)
        if current_version is None:
            return None
        candidates: list[tuple[tuple[int, int, int], str]] = []
        for page in range(1, MAX_API_PAGES + 1):
            tags = self._request(
                "GET", f"/repos/{repository}/tags?per_page=100&page={page}"
            )
            if not isinstance(tags, list):
                raise ValueError("GitHub returned invalid tag history")
            for tag in tags:
                name = tag.get("name") if isinstance(tag, dict) else None
                version = self._version_tuple(name)
                if (
                    isinstance(name, str)
                    and version is not None
                    and version < current_version
                ):
                    candidates.append((version, name))
            if len(tags) < 100:
                break
        else:
            raise ValueError("Tag history exceeds the supported pagination limit")
        for _version, name in sorted(candidates, reverse=True):
            if self._is_ancestor_tag(repository, name, current_tag):
                return name
        return None

    def _is_ancestor_tag(
        self, repository: str, candidate_tag: str, current_tag: str
    ) -> bool:
        encoded_range = (
            f"{quote(candidate_tag, safe='')}...{quote(current_tag, safe='')}"
        )
        response = self._request("GET", f"/repos/{repository}/compare/{encoded_range}")
        if not isinstance(response, dict) or not isinstance(
            response.get("status"), str
        ):
            raise ValueError("GitHub returned invalid tag ancestry data")
        return response["status"] in {"ahead", "identical"}

    def _pull_requests_between(
        self, repository: str, previous_tag: str, current_tag: str
    ) -> list[ReleasedPullRequest]:
        encoded_range = (
            f"{quote(previous_tag, safe='')}...{quote(current_tag, safe='')}"
        )
        commits: list[dict[str, Any]] = []
        total_commits: int | None = None
        for page in range(1, MAX_API_PAGES + 1):
            response = self._request(
                "GET",
                f"/repos/{repository}/compare/{encoded_range}?per_page=100&page={page}",
            )
            if not isinstance(response, dict) or not isinstance(
                response.get("commits"), list
            ):
                raise ValueError("GitHub returned invalid release comparison data")
            page_commits = response["commits"]
            commits.extend(page_commits)
            total_commits = response.get("total_commits", total_commits)
            if len(page_commits) < 100 or (
                isinstance(total_commits, int) and len(commits) >= total_commits
            ):
                break
        if isinstance(total_commits, int) and len(commits) < total_commits:
            raise ValueError(
                "Release comparison exceeds the supported pagination limit"
            )

        pull_requests: dict[int, tuple[dict[str, Any], list[str]]] = {}
        for commit in commits:
            sha = commit.get("sha") if isinstance(commit, dict) else None
            if not isinstance(sha, str) or not sha:
                raise ValueError("Release comparison contains a commit without a SHA")
            commit_data = commit.get("commit")
            message = (
                commit_data.get("message") if isinstance(commit_data, dict) else None
            )
            if not isinstance(message, str) or not message:
                raise ValueError(
                    "Release comparison contains a commit without a message"
                )
            response = self._request(
                "GET", f"/repos/{repository}/commits/{sha}/pulls?per_page=100"
            )
            if not isinstance(response, list):
                raise ValueError("GitHub returned invalid pull request metadata")
            for pull_request in response:
                number = (
                    pull_request.get("number")
                    if isinstance(pull_request, dict)
                    else None
                )
                if isinstance(number, int) and pull_request.get("merged_at"):
                    metadata, messages = pull_requests.setdefault(
                        number, (pull_request, [])
                    )
                    if message not in messages:
                        messages.append(message)
        return [
            ReleasedPullRequest(metadata=metadata, commit_messages=tuple(messages))
            for metadata, messages in pull_requests.values()
        ]

    def _require_successful_checks(self, repository: str, head_sha: str) -> None:
        checks: list[dict[str, Any]] = []
        total_count: int | None = None
        for page in range(1, MAX_API_PAGES + 1):
            response = self._request(
                "GET",
                f"/repos/{repository}/commits/{head_sha}/check-runs"
                f"?filter=latest&per_page=100&page={page}",
            )
            if not isinstance(response, dict) or not isinstance(
                response.get("check_runs"), list
            ):
                raise ValueError("GitHub returned invalid check-run data")
            page_checks = response["check_runs"]
            checks.extend(page_checks)
            total_count = response.get("total_count", total_count)
            if len(page_checks) < 100 or (
                isinstance(total_count, int) and len(checks) >= total_count
            ):
                break
        if isinstance(total_count, int) and len(checks) < total_count:
            raise ValueError("Check runs exceed the supported pagination limit")
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

    def _record_issue(
        self,
        repository: str,
        issue_number: int,
        pull_urls: tuple[str, ...],
        release_url: str,
    ) -> bool:
        issue_path = f"/repos/{repository}/issues/{issue_number}"
        issue = self._request("GET", issue_path)
        if (
            not isinstance(issue, dict)
            or issue.get("state") != "open"
            or "pull_request" in issue
        ):
            return False
        labels = self._label_names(issue)
        if "solved" in labels:
            return False
        marker = delivery_marker(pull_urls, release_url)
        marker_exists = self._has_comment_marker(issue_path, marker)
        already_awaiting = "awaiting confirmation" in labels
        if not already_awaiting:
            self._request(
                "POST", f"{issue_path}/labels", {"labels": ["awaiting confirmation"]}
            )
        for label in ("needs information", "needs investigation", "agent ready"):
            if label in labels:
                self._request("DELETE", f"{issue_path}/labels/{quote(label, safe='')}")
        live_issue = self._request("GET", issue_path)
        live_labels = (
            self._label_names(live_issue) if isinstance(live_issue, dict) else set()
        )
        if (
            not isinstance(live_issue, dict)
            or live_issue.get("state") != "open"
            or "pull_request" in live_issue
            or "solved" in live_labels
        ):
            if not already_awaiting:
                self._request(
                    "DELETE",
                    f"{issue_path}/labels/{quote('awaiting confirmation', safe='')}",
                )
            return False

        comment_added = False
        if not marker_exists:
            pull_description = (
                pull_urls[0]
                if len(pull_urls) == 1
                else ", ".join(pull_urls[:-1]) + f" and {pull_urls[-1]}"
            )
            posted_comment = self._request(
                "POST",
                f"{issue_path}/comments",
                {
                    "body": f"{marker}\nFix delivered in {pull_description} and released "
                    f"as {release_url}. Waiting for the reporter to confirm with "
                    "`/powersync solved`."
                },
            )
            comment_id = (
                posted_comment.get("id") if isinstance(posted_comment, dict) else None
            )
            final_issue = self._request("GET", issue_path)
            final_labels = (
                self._label_names(final_issue)
                if isinstance(final_issue, dict)
                else set()
            )
            if (
                not isinstance(final_issue, dict)
                or final_issue.get("state") != "open"
                or "pull_request" in final_issue
                or "solved" in final_labels
            ):
                if not isinstance(comment_id, int):
                    raise ValueError(
                        "GitHub did not return the delivery comment ID needed for rollback"
                    )
                self._request(
                    "DELETE", f"/repos/{repository}/issues/comments/{comment_id}"
                )
                if not already_awaiting:
                    self._request(
                        "DELETE",
                        f"{issue_path}/labels/{quote('awaiting confirmation', safe='')}",
                    )
                return False
            comment_added = True
        return not already_awaiting or comment_added

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
    def _referenced_issues(repository: str, body: Any) -> set[int]:
        if not isinstance(body, str):
            return set()
        body_without_comments = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
        escaped_repository = re.escape(repository)
        pattern = re.compile(
            rf"(?im)\bRefs\s+(?:#|{escaped_repository}#|"
            rf"https://github\.com/{escaped_repository}/issues/)(\d+)\b"
        )
        return {int(number) for number in pattern.findall(body_without_comments)}

    @staticmethod
    def _label_names(issue: dict[str, Any]) -> set[str]:
        return {
            label.get("name", "")
            for label in issue.get("labels", [])
            if isinstance(label, dict)
        }

    @staticmethod
    def _parse_timestamp(value: Any, field: str) -> datetime:
        if not isinstance(value, str) or not value:
            raise ValueError(f"The supplied evidence has no {field} timestamp")
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def _version_tuple(value: Any) -> tuple[int, int, int] | None:
        if not isinstance(value, str):
            return None
        match = VERSION_TAG_PATTERN.fullmatch(value)
        if match is None:
            return None
        major, minor, patch = match.groups()
        return int(major), int(minor), int(patch)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        return self._client.request(method, path, payload)
