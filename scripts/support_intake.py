"""Fail-closed evidence checks before any support content reaches a model."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlparse

SANITISED_MARKER = "PowerSync sanitised support bundle v1"
WARNING_MARKER = "<!-- powersync-intake:v1:unsafe -->"
MAX_ATTACHMENT_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_ATTACHMENTS = 5
MAX_TEXT_CHARACTERS = 200_000
MAX_COMMENT_PAGES = 10
ALLOWED_EXTENSIONS = frozenset(
    {
        ".txt",
        ".log",
        ".json",
        ".jsonc",
        ".yaml",
        ".yml",
        ".csv",
        ".tsv",
        ".md",
        ".debug",
    }
)
ATTACHMENT_PATTERN = re.compile(
    r"!?\[([^\]]*)\]\((https://github\.com/user-attachments/[^)\s]+)\)"
)
SECRET_PATTERNS = (
    re.compile(r"(?i)authorization\s*:\s*(?:bearer|basic)\s+\S+"),
    re.compile(
        r"(?i)(?:password|passwd|access[_ -]?token|api[_ -]?key|client[_ -]?secret)"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{8,}"
    ),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"https://(?:discord(?:app)?\.com/api/webhooks|hooks\.slack\.com/services)/\S+"
    ),
)


class IntakeGitHubApi(Protocol):
    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any: ...

    def download(self, url: str, maximum_bytes: int) -> bytes: ...


@dataclass(frozen=True)
class IntakeDecision:
    safe: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Attachment:
    name: str
    url: str


class SupportIntake:
    """Inspect current issue evidence, update safety state, and dispatch triage."""

    def __init__(self, client: IntakeGitHubApi) -> None:
        self._client = client

    def inspect(self, repository: str, issue_number: int) -> IntakeDecision:
        issue_path = f"/repos/{repository}/issues/{issue_number}"
        issue = self._client.request("GET", issue_path)
        comments, comment_limit_reached = self._load_comments(issue_path)
        if not isinstance(issue, dict):
            raise ValueError("GitHub returned invalid issue evidence")

        text_parts = [str(issue.get("title", "")), str(issue.get("body", ""))]
        text_parts.extend(str(comment.get("body", "")) for comment in comments)
        combined_text = "\n".join(text_parts)
        reasons = self._inspect_text(combined_text)
        if comment_limit_reached:
            reasons.append("issue exceeds the support comment limit")
        reasons.extend(self._inspect_attachments(combined_text))
        decision = IntakeDecision(
            safe=not reasons, reasons=tuple(dict.fromkeys(reasons))
        )
        if decision.safe:
            self._record_safe(repository, issue_number)
        else:
            self._record_unsafe(repository, issue_number, comments, decision.reasons)
        return decision

    def _load_comments(self, issue_path: str) -> tuple[list[dict[str, Any]], bool]:
        comments: list[dict[str, Any]] = []
        for page in range(1, MAX_COMMENT_PAGES + 1):
            response = self._client.request(
                "GET", f"{issue_path}/comments?per_page=100&page={page}"
            )
            if not isinstance(response, list) or not all(
                isinstance(comment, dict) for comment in response
            ):
                raise ValueError("GitHub returned invalid issue comments")
            comments.extend(response)
            if len(response) < 100:
                return comments, False
        return comments, True

    @staticmethod
    def _inspect_text(text: str) -> list[str]:
        reasons: list[str] = []
        if len(text) > MAX_TEXT_CHARACTERS:
            reasons.append("issue text exceeds the support evidence size limit")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            reasons.append("possible credential in issue text")
        return reasons

    def _inspect_attachments(self, text: str) -> list[str]:
        attachments = self._attachments(text)
        if len(attachments) > MAX_ATTACHMENTS:
            return [f"more than {MAX_ATTACHMENTS} attachments were supplied"]

        reasons: list[str] = []
        total_bytes = 0
        for attachment in attachments:
            extension = self._extension(attachment.name)
            if extension not in ALLOWED_EXTENSIONS:
                reasons.append(
                    f"{attachment.name} is not an allowed text evidence format"
                )
                continue
            try:
                content = self._client.download(attachment.url, MAX_ATTACHMENT_BYTES)
                total_bytes += len(content)
                if total_bytes > MAX_TOTAL_BYTES:
                    raise ValueError(
                        "combined attachments exceed the support evidence size limit"
                    )
                decoded = content.decode("utf-8")
            except (UnicodeDecodeError, ValueError) as error:
                reasons.append(f"{attachment.name}: {error}")
                continue
            if "\x00" in decoded:
                reasons.append(f"{attachment.name} contains binary data")
                continue
            if not decoded.startswith(SANITISED_MARKER):
                reasons.append(
                    f"{attachment.name} is missing the sanitised support bundle marker"
                )
            if any(pattern.search(decoded) for pattern in SECRET_PATTERNS):
                reasons.append(
                    f"{attachment.name} still contains a possible credential"
                )
            if self._excessively_nested(decoded, extension):
                reasons.append(f"{attachment.name} exceeds the supported nesting depth")
        return reasons

    @staticmethod
    def _attachments(text: str) -> list[Attachment]:
        found: dict[str, Attachment] = {}
        for name, url in ATTACHMENT_PATTERN.findall(text):
            url_name = urlparse(url).path.rsplit("/", 1)[-1]
            resolved_name = name.strip()
            if not SupportIntake._extension(resolved_name):
                resolved_name = url_name
            found[url] = Attachment(name=resolved_name, url=url)
        return list(found.values())

    @staticmethod
    def _extension(name: str) -> str:
        lowered = name.casefold().split("?", 1)[0]
        position = lowered.rfind(".")
        return lowered[position:] if position >= 0 else ""

    @staticmethod
    def _excessively_nested(content: str, extension: str) -> bool:
        if extension in {".json", ".jsonc"}:
            depth = 0
            for character in content:
                if character in "[{":
                    depth += 1
                    if depth > 20:
                        return True
                elif character in "]}":
                    depth = max(0, depth - 1)
        if extension in {".yaml", ".yml"}:
            return any(
                len(line) - len(line.lstrip(" ")) > 40 for line in content.splitlines()
            )
        return False

    def _record_safe(self, repository: str, issue_number: int) -> None:
        issue_path = f"/repos/{repository}/issues/{issue_number}"
        self._client.request(
            "POST", f"{issue_path}/labels", {"labels": ["safe evidence"]}
        )
        self._client.request(
            "DELETE", f"{issue_path}/labels/{quote('unsafe evidence', safe='')}"
        )
        self._client.request(
            "POST",
            f"/repos/{repository}/actions/workflows/issue-triage.lock.yml/dispatches",
            {"ref": "main", "inputs": {"issue_number": str(issue_number)}},
        )

    def _record_unsafe(
        self,
        repository: str,
        issue_number: int,
        comments: list[dict[str, Any]],
        reasons: tuple[str, ...],
    ) -> None:
        issue_path = f"/repos/{repository}/issues/{issue_number}"
        self._client.request(
            "POST", f"{issue_path}/labels", {"labels": ["unsafe evidence"]}
        )
        for label in ("safe evidence", "needs investigation"):
            self._client.request(
                "DELETE", f"{issue_path}/labels/{quote(label, safe='')}"
            )
        if any(WARNING_MARKER in str(comment.get("body", "")) for comment in comments):
            return
        reason_list = "\n".join(f"- {reason}" for reason in reasons)
        body = (
            f"{WARNING_MARKER}\nSupport evidence was blocked before AI processing:\n\n"
            f"{reason_list}\n\nCreate a `{SANITISED_MARKER}` file with the PowerSync "
            "support-bundle tool, remove the unsafe attachment or text, and then edit "
            "the issue or add a new comment. Only bounded text evidence is accepted. "
            "If a real credential was uploaded, revoke or rotate it immediately; removing "
            "the link does not guarantee confidentiality."
        )
        self._client.request("POST", f"{issue_path}/comments", {"body": body})
