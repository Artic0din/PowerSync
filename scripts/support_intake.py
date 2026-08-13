"""Fail-closed evidence checks before any support content reaches a model."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlparse

SANITISED_MARKER = "PowerSync sanitised support bundle v1"
WARNING_MARKER = "<!-- powersync-intake:v1:unsafe -->"
WORKFLOW_BOT_LOGIN = "github-actions[bot]"
MAX_ATTACHMENT_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_ATTACHMENTS = 5
MAX_TEXT_CHARACTERS = 200_000
MAX_COMMENT_PAGES = 10
SAFETY_LABELS = frozenset({"safe evidence", "unsafe evidence"})
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
ATTACHMENT_URL_PATTERN = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
MARKDOWN_ATTACHMENT_PATTERN = re.compile(
    r"!?\[([^\]]*)\]\((https?://[^)\s]+)\)", re.IGNORECASE
)
SECRET_PATTERNS = (
    re.compile(
        r"[\"']?authorization[\"']?\s*:\s*[\"']?(?:bearer|basic)\s+"
        r"[^\"'\s,}]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"[\"']?(?:set-cookie|cookie)[\"']?\s*:"
        r"(?![ \t]*[\"']?\[REDACTED\][\"']?[ \t]*(?:[,}]|\r?\n|\Z))"
        r"(?:\"(?:\\[^\r\n]|[^\"\\\r\n])*\"|"
        r"'(?:\\[^\r\n]|[^'\\\r\n])*'|[^\r\n]+)",
        re.IGNORECASE,
    ),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bpsk_[A-Za-z0-9]{20,}\b", re.IGNORECASE),
    re.compile(r"\bpsync_[A-Za-z0-9_-]{43}\b"),
    re.compile(r"\bxai-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"https://(?:discord(?:app)?\.com/api/webhooks|hooks\.slack\.com/services)/\S+"
    ),
)
KEYED_SECRET_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9_-])[\"']?(?!timezone[_ -]?token[\"']?\s*[:=])"
    r"(?:(?:[A-Z0-9]+[_ -]+)*(?:password|passwd|pass[_ -]?enc|token|"
    r"api[_ -]?key|app[_ -]?secret|client[_ -]?secret|"
    r"private[_ -]?key(?:[_ -]?(?:pem|der))?)|cookie)[\"']?\s*[:=]\s*"
    r"(?P<value>\"(?:\\[^\r\n]|[^\"\\\r\n])*\"|"
    r"'(?:\\[^\r\n]|[^'\\\r\n])*'|"
    r"(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n]*)"
)
EXACT_REDACTED_VALUE = re.compile(r"^[\"']?\[REDACTED\][\"']?\s*$", re.IGNORECASE)
EXACT_EMPTY_VALUE = re.compile(r"^(?:null|true|false)\s*$", re.IGNORECASE)
IPV4_PATTERN = re.compile(
    r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b"
)
VERSION_CONTEXT_PATTERN = re.compile(
    r"(?:version|firmware|integration)\s*[:=]?\s*$", re.IGNORECASE
)
IDENTIFIER_PATTERNS = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}\b", re.IGNORECASE),
    re.compile(
        r"(?<![0-9A-F:])(?:(?:[0-9A-F]{1,4}:){1,6}:|::)"
        r"(?:ffff(?::0{1,4})?:)?"
        r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"
        r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![0-9A-F:])(?:"
        r"(?:[0-9A-F]{1,4}:){7}[0-9A-F]{1,4}|"
        r"(?:[0-9A-F]{1,4}:){1,7}:|"
        r"(?:[0-9A-F]{1,4}:){1,6}:[0-9A-F]{1,4}|"
        r"(?:[0-9A-F]{1,4}:){1,5}(?::[0-9A-F]{1,4}){1,2}|"
        r"(?:[0-9A-F]{1,4}:){1,4}(?::[0-9A-F]{1,4}){1,3}|"
        r"(?:[0-9A-F]{1,4}:){1,3}(?::[0-9A-F]{1,4}){1,4}|"
        r"(?:[0-9A-F]{1,4}:){1,2}(?::[0-9A-F]{1,4}){1,5}|"
        r"[0-9A-F]{1,4}:(?:(?::[0-9A-F]{1,4}){1,6})|"
        r":(?:(?::[0-9A-F]{1,4}){1,7}|:)"
        r")(?![0-9A-F:])",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?=[A-HJ-NPR-Z0-9]{17}\b)(?=[A-HJ-NPR-Z0-9]*[A-HJ-NPR-Z])"
        r"(?=[A-HJ-NPR-Z0-9]*\d)[A-HJ-NPR-Z0-9]{17}\b",
        re.IGNORECASE,
    ),
    re.compile(r"/(?:Users|home)/[^/\r\n]+"),
    re.compile(r"[A-Z]:\\Users\\[^\\\r\n]+", re.IGNORECASE),
)
KEYED_IDENTIFIER_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9_-])[\"']?(?P<kind>"
    r"(?:[A-Z0-9]+[_ -]+)*serial(?:[_ -]?number)?|"
    r"(?:[A-Z0-9]+[_ -]+)*device[_ -]?id|"
    r"(?:[A-Z0-9]+[_ -]+)*user(?:name)?|(?:[A-Z0-9]+[_ -]+)*login|"
    r"(?:[A-Z0-9]+[_ -]+)*gateway[_ -]?id|"
    r"(?:[A-Z0-9]+[_ -]+)*site[_ -]?id|din|warp[_ -]?site[_ -]?number|"
    r"energy[_ -]?site|account[_ -]?number|site[_ -]?address)"
    r"[\"']?\s*[:=]\s*"
    r"(?P<value>\"(?:\\[^\r\n]|[^\"\\\r\n])*\"|"
    r"'(?:\\[^\r\n]|[^'\\\r\n])*'|"
    r"(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n]+)"
)
PREFIXED_IDENTIFIER_PATTERNS = (
    re.compile(r"\bfor site\s+[A-Za-z0-9-]{15,}", re.IGNORECASE),
    re.compile(r"\bsite\s+\d{13,}", re.IGNORECASE),
    re.compile(r"\benergy_sites?[/\s:=]+\d{13,}", re.IGNORECASE),
)
EXACT_IDENTIFIER_VALUE = re.compile(
    r"^[\"']?\[(?:SERIAL|USER|DEVICE)_\d+\][\"']?\s*$", re.IGNORECASE
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
class IntakeSnapshot:
    decision: IntakeDecision
    content: str
    labels: frozenset[str]
    warning_posted: bool = False


@dataclass(frozen=True)
class Attachment:
    name: str
    url: str


def snapshot_revision(snapshot: IntakeSnapshot) -> str:
    evidence_content = re.sub(r"\n\nLabels: [^\n]*", "", snapshot.content, count=1)
    canonical = json.dumps(
        {
            "content": evidence_content,
            "labels": sorted(snapshot.labels - SAFETY_LABELS),
            "safe": snapshot.decision.safe,
            "reasons": snapshot.decision.reasons,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SupportIntake:
    """Inspect current issue evidence, update safety state, and dispatch triage."""

    def __init__(self, client: IntakeGitHubApi) -> None:
        self._client = client

    def inspect(self, repository: str, issue_number: int) -> IntakeDecision:
        snapshot = self.evaluate(repository, issue_number)
        if snapshot.decision.safe:
            self._record_safe(repository, issue_number, snapshot_revision(snapshot))
        else:
            self._record_unsafe(
                repository,
                issue_number,
                snapshot.decision.reasons,
                snapshot.warning_posted,
            )
        return snapshot.decision

    def evaluate(self, repository: str, issue_number: int) -> IntakeSnapshot:
        """Capture and inspect the exact evidence revision a model may read."""

        issue_path = f"/repos/{repository}/issues/{issue_number}"
        issue = self._client.request("GET", issue_path)
        if not isinstance(issue, dict):
            raise ValueError("GitHub returned invalid issue evidence")
        comments, comment_limit_reached = self._load_comments(issue_path)

        text_parts = [str(issue.get("title", "")), str(issue.get("body", ""))]
        text_parts.extend(str(comment.get("body", "")) for comment in comments)
        combined_text = "\n".join(text_parts)
        reasons = self._inspect_text(combined_text)
        if issue.get("state") != "open":
            reasons.append("issue is not open")
        if comment_limit_reached:
            reasons.append("issue exceeds the support comment limit")
        attachment_reasons, attachment_contents = self._inspect_attachments(
            combined_text
        )
        reasons.extend(attachment_reasons)
        decision = IntakeDecision(
            safe=not reasons, reasons=tuple(dict.fromkeys(reasons))
        )
        labels = frozenset(
            str(label.get("name", ""))
            for label in issue.get("labels", [])
            if isinstance(label, dict)
        )
        snapshot_parts = [
            f"# PowerSync support issue #{issue_number}",
            f"Author: {self._author_login(issue)}",
            f"Labels: {', '.join(sorted(labels))}",
            f"Title: {issue.get('title', '')}",
            "## Issue body",
            str(issue.get("body", "")),
        ]
        for comment in comments:
            snapshot_parts.extend(
                (
                    f"## Comment by {self._author_login(comment)}",
                    str(comment.get("body", "")),
                )
            )
        for attachment, content in attachment_contents:
            snapshot_parts.extend((f"## Attachment: {attachment.name}", content))
        return IntakeSnapshot(
            decision=decision,
            content="\n\n".join(snapshot_parts),
            labels=labels,
            warning_posted=any(
                WARNING_MARKER in str(comment.get("body", ""))
                and self._author_login(comment) == WORKFLOW_BOT_LOGIN
                for comment in comments
            ),
        )

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
        if SupportIntake._contains_secret(text):
            reasons.append("possible credential in issue text")
        if SupportIntake._contains_identifier(text):
            reasons.append("personal identifier in issue text")
        return reasons

    @staticmethod
    def _contains_secret(text: str) -> bool:
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            return True
        return any(
            not EXACT_REDACTED_VALUE.fullmatch(match.group("value").strip())
            and not EXACT_EMPTY_VALUE.fullmatch(match.group("value").strip())
            for match in KEYED_SECRET_PATTERN.finditer(text)
        )

    @staticmethod
    def _contains_identifier(text: str) -> bool:
        if any(pattern.search(text) for pattern in IDENTIFIER_PATTERNS):
            return True
        if any(pattern.search(text) for pattern in PREFIXED_IDENTIFIER_PATTERNS):
            return True
        if any(
            not EXACT_IDENTIFIER_VALUE.fullmatch(match.group("value").strip())
            and not EXACT_EMPTY_VALUE.fullmatch(match.group("value").strip())
            for match in KEYED_IDENTIFIER_PATTERN.finditer(text)
        ):
            return True
        return any(
            VERSION_CONTEXT_PATTERN.search(
                text[max(0, match.start() - 32) : match.start()]
            )
            is None
            for match in IPV4_PATTERN.finditer(text)
        )

    def _inspect_attachments(
        self, text: str
    ) -> tuple[list[str], list[tuple[Attachment, str]]]:
        attachments = self._attachments(text)
        if len(attachments) > MAX_ATTACHMENTS:
            return [f"more than {MAX_ATTACHMENTS} attachments were supplied"], []

        reasons: list[str] = []
        contents: list[tuple[Attachment, str]] = []
        total_bytes = 0
        for index, attachment in enumerate(attachments, start=1):
            attachment_label = f"attachment {index}"
            extension = self._extension(attachment.name)
            if extension not in ALLOWED_EXTENSIONS:
                reasons.append(
                    f"{attachment_label} is not an allowed text evidence format"
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
                reasons.append(f"{attachment_label}: {error}")
                continue
            if "\x00" in decoded:
                reasons.append(f"{attachment_label} contains binary data")
                continue
            if not decoded.startswith(SANITISED_MARKER):
                reasons.append(
                    f"{attachment_label} is missing the sanitised support bundle marker"
                )
            if self._contains_secret(decoded):
                reasons.append(
                    f"{attachment_label} still contains a possible credential"
                )
            if self._contains_identifier(decoded):
                reasons.append(
                    f"{attachment_label} still contains a personal identifier"
                )
            if self._excessively_nested(decoded, extension):
                reasons.append(
                    f"{attachment_label} exceeds the supported nesting depth"
                )
            contents.append((attachment, decoded))
        return reasons, contents

    @staticmethod
    def _author_login(item: dict[str, Any]) -> str:
        user = item.get("user")
        if isinstance(user, dict) and isinstance(user.get("login"), str):
            return user["login"]
        return "unknown"

    @staticmethod
    def _attachments(text: str) -> list[Attachment]:
        markdown_names = {
            url: name.strip()
            for name, url in MARKDOWN_ATTACHMENT_PATTERN.findall(text)
            if SupportIntake._is_attachment_url(url)
        }
        found: dict[str, Attachment] = {}
        for url in ATTACHMENT_URL_PATTERN.findall(text):
            if not SupportIntake._is_attachment_url(url):
                continue
            url_name = urlparse(url).path.rsplit("/", 1)[-1]
            resolved_name = markdown_names.get(url, "")
            if not SupportIntake._extension(resolved_name):
                resolved_name = url_name
            found[url] = Attachment(name=resolved_name, url=url)
        return list(found.values())

    @staticmethod
    def _is_attachment_url(url: str) -> bool:
        parsed = urlparse(url)
        return (
            parsed.scheme.casefold() == "https"
            and (parsed.hostname or "").casefold() == "github.com"
            and parsed.path.startswith("/user-attachments/")
        )

    @staticmethod
    def _extension(name: str) -> str:
        lowered = name.casefold().split("?", 1)[0]
        position = lowered.rfind(".")
        return lowered[position:] if position >= 0 else ""

    @staticmethod
    def _excessively_nested(content: str, extension: str) -> bool:
        if extension in {".json", ".jsonc"}:
            depth = 0
            in_string = False
            escaped = False
            for character in content:
                if in_string:
                    if escaped:
                        escaped = False
                    elif character == "\\":
                        escaped = True
                    elif character == '"':
                        in_string = False
                    continue
                if character == '"':
                    in_string = True
                    continue
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

    def _record_safe(
        self, repository: str, issue_number: int, evidence_revision: str
    ) -> None:
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
            {
                "ref": "main",
                "inputs": {
                    "issue_number": str(issue_number),
                    "evidence_revision": evidence_revision,
                },
            },
        )

    def _record_unsafe(
        self,
        repository: str,
        issue_number: int,
        reasons: tuple[str, ...],
        warning_posted: bool,
    ) -> None:
        issue_path = f"/repos/{repository}/issues/{issue_number}"
        self._client.request(
            "POST", f"{issue_path}/labels", {"labels": ["unsafe evidence"]}
        )
        for label in ("safe evidence", "needs investigation"):
            self._client.request(
                "DELETE", f"{issue_path}/labels/{quote(label, safe='')}"
            )
        if warning_posted:
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
