"""Fail-closed evidence checks before any support content reaches a model."""

from __future__ import annotations

import hashlib
import html
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
        r"[\"']?authorization[\"']?\s*:"
        r"(?![ \t]*[\"']?\[REDACTED\][\"']?[ \t]*(?:[,}]|\r?\n|\Z))"
        r"[ \t]*"
        r"(?:\"(?:\\[^\r\n]|[^\"\\\r\n])*\"|"
        r"'(?:\\[^\r\n]|[^'\\\r\n])*'|[^\r\n]+)",
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
    re.compile(r"(?:Exponent|Expo)PushToken\[[^\]\s]{10,}\]", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_-]{20,}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"https://(?:discord(?:app)?\.com/api/webhooks|hooks\.slack\.com/services)/\S+",
        re.IGNORECASE,
    ),
)
KEYED_SECRET_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9_-])[\"']?(?!timezone[_ -]?token[\"']?\s*[:=])"
    r"(?:(?:[A-Z0-9]+[_ -]+)*(?:password|passwd|pass[_ -]?enc|token|"
    r"api[_ -]?key|app[_ -]?secret|client[_ -]?secret|"
    r"private[_ -]?key(?:[_ -]?(?:pem|der))?)|accessToken|refreshToken|"
    r"apiKey|appSecret|clientSecret|privateKey(?:Pem|Der)?|passEnc|cookie)"
    r"[\"']?\s*[:=]\s*"
    r"(?P<value>\"(?:\\[^\r\n]|[^\"\\\r\n])*\"|"
    r"'(?:\\[^\r\n]|[^'\\\r\n])*'|"
    r"(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n]*)"
)
SECRET_KEY_NAME_PATTERN = re.compile(
    r"(?i)^(?!timezone[_ -]?token$)"
    r"(?:(?:[A-Z0-9]+[_ -]+)*(?:password|passwd|pass[_ -]?enc|token|"
    r"api[_ -]?key|app[_ -]?secret|client[_ -]?secret|"
    r"private[_ -]?key(?:[_ -]?(?:pem|der))?)|accessToken|refreshToken|"
    r"apiKey|appSecret|clientSecret|privateKey(?:Pem|Der)?|passEnc|cookie|"
    r"authorization|set[_ -]?cookie)$"
)
JSON_KEY_PATTERN = re.compile(r'"(?P<key>(?:\\.|[^"\\])*)"(?P<delimiter>\s*:)')
JSON_UNICODE_ESCAPE_PATTERN = re.compile(r"\\u(?P<digits>[0-9a-f]{4})", re.IGNORECASE)
URL_USERINFO_PATTERN = re.compile(
    r"\b[a-z][a-z0-9+.-]*://(?P<userinfo>[^/@\s]+)@", re.IGNORECASE
)
YAML_SECRET_SCALAR_HEADER_PATTERN = re.compile(
    r"(?i)^(?P<indent>[ \t]*)(?:-[ \t]+)?[\"']?"
    r"(?!timezone[_ -]?token[\"']?\s*:)"
    r"(?:(?:[A-Z0-9]+[_ -]+)*(?:password|passwd|pass[_ -]?enc|token|"
    r"api[_ -]?key|app[_ -]?secret|client[_ -]?secret|"
    r"private[_ -]?key(?:[_ -]?(?:pem|der))?)|accessToken|refreshToken|"
    r"apiKey|appSecret|clientSecret|privateKey(?:Pem|Der)?|passEnc|cookie)"
    r"[\"']?\s*:\s*(?P<value>.*)$"
)
COOKIE_ARRAY_HEADER_PATTERN = re.compile(
    r"[\"']?cookies[\"']?\s*:\s*\[", re.IGNORECASE
)
COOKIE_VALUE_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9_-])[\"']?value[\"']?\s*:\s*"
    r"(?P<value>\"(?:\\[^\r\n]|[^\"\\\r\n])*\"|"
    r"'(?:\\[^\r\n]|[^'\\\r\n])*'|"
    r"(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n,}\]]+)"
)
EXACT_REDACTED_VALUE = re.compile(r"^[\"']?\[REDACTED\][\"']?\s*$", re.IGNORECASE)
EXACT_EMPTY_VALUE = re.compile(r"^(?:null|true|false)\s*$", re.IGNORECASE)
IPV4_PATTERN = re.compile(
    r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b"
)
VERSION_CONTEXT_PATTERN = re.compile(
    r"(?:version|firmware[_ -]?version|integration[_ -]?version)\s*[:=]?\s*$",
    re.IGNORECASE,
)
IDENTIFIER_PATTERNS = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}|"
        r"(?:[0-9A-F]{4}\.){2}[0-9A-F]{4})\b",
        re.IGNORECASE,
    ),
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
    r"[A-Z0-9_-]*serial(?:[_ -]?number)?|"
    r"[A-Z0-9_-]*device[_ -]?(?:id|sn|name)|"
    r"[A-Z0-9_-]*user(?:name)?|[A-Z0-9_-]*login|"
    r"[A-Z0-9_-]*gateway[_ -]?id|"
    r"[A-Z0-9_-]*site[_ -]?(?:id|identifier)|din|nmi|"
    r"warp[_ -]?site[_ -]?number|energy[_ -]?site|"
    r"account[_ -]?(?:number|name|address)|site[_ -]?address|"
    r"concession[_ -]?address|street[_ -]?address|document[_ -]?id|"
    r"email[_ -]?address|invoice[_ -]?number|identifier)"
    r"[\"']?\s*[:=]\s*"
    r"(?P<value>\"(?:\\[^\r\n]|[^\"\\\r\n])*\"|"
    r"'(?:\\[^\r\n]|[^'\\\r\n])*'|"
    r"(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n]+)"
)
ADDRESS_IDENTIFIER_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9_-])[\"']?address[\"']?\s*[:=]\s*"
    r"(?P<value>\"(?:\\[^\r\n]|[^\"\\\r\n])*\"|"
    r"'(?:\\[^\r\n]|[^'\\\r\n])*'|"
    r"(?:null|true|false|-?\d+(?:\.\d+)?)(?=\s*[,}])|[^\r\n]+)"
)
LOG_DEVICE_IDENTIFIER_PATTERN = re.compile(
    r"\bdevice\s*=\s*(?P<value>.*?)(?=,\s*[A-Z0-9_]+\s*=|$)",
    re.IGNORECASE | re.MULTILINE,
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
        decoded_variants, exhausted = SupportIntake._html_decoded_variants(text)
        if exhausted:
            return True
        for candidate in decoded_variants:
            candidate = SupportIntake._normalize_json_key_escapes(candidate)
            if SupportIntake._contains_yaml_secret(candidate):
                return True
            if SupportIntake._contains_cookie_jar_secret(candidate):
                return True
            if any(pattern.search(candidate) for pattern in SECRET_PATTERNS):
                return True
            if SupportIntake._contains_escaped_keyed_secret(candidate):
                return True
            if URL_USERINFO_PATTERN.search(candidate):
                return True
            if any(
                not EXACT_REDACTED_VALUE.fullmatch(match.group("value").strip())
                and not EXACT_EMPTY_VALUE.fullmatch(match.group("value").strip())
                for match in KEYED_SECRET_PATTERN.finditer(candidate)
            ):
                return True
        return False

    @staticmethod
    def _normalize_json_key_escapes(text: str) -> str:
        def normalize(match: re.Match[str]) -> str:
            key = JSON_UNICODE_ESCAPE_PATTERN.sub(
                lambda escape: chr(int(escape.group("digits"), 16)),
                match.group("key"),
            )
            return f'"{key}"{match.group("delimiter")}'

        return JSON_KEY_PATTERN.sub(normalize, text)

    @staticmethod
    def _html_decoded_variants(text: str) -> tuple[tuple[str, ...], bool]:
        variants = [text]
        for _pass in range(32):
            decoded = html.unescape(variants[-1])
            if decoded == variants[-1]:
                return tuple(variants), False
            variants.append(decoded)
        return tuple(variants), html.unescape(variants[-1]) != variants[-1]

    @staticmethod
    def _contains_yaml_secret(text: str) -> bool:
        lines = text.splitlines()
        for index, line in enumerate(lines):
            header = YAML_SECRET_SCALAR_HEADER_PATTERN.fullmatch(line)
            if header is None:
                continue
            value = header.group("value").strip()
            if value.endswith(","):
                value = value[:-1].rstrip()
            if not EXACT_REDACTED_VALUE.fullmatch(
                value
            ) and not EXACT_EMPTY_VALUE.fullmatch(value):
                return True
            base_indent = len(header.group("indent"))
            for continuation in lines[index + 1 :]:
                if not continuation.strip():
                    continue
                indentation = len(continuation) - len(
                    continuation.lstrip(" \t")
                )
                if indentation <= base_indent:
                    break
                if not EXACT_REDACTED_VALUE.fullmatch(continuation.strip()):
                    return True
        return False

    @staticmethod
    def _contains_cookie_jar_secret(text: str) -> bool:
        for header in COOKIE_ARRAY_HEADER_PATTERN.finditer(text):
            array_start = header.end() - 1
            array_end = SupportIntake._matching_json_array_end(text, array_start)
            if array_end is None:
                return True
            cookie_array = text[array_start : array_end + 1]
            if any(
                not EXACT_REDACTED_VALUE.fullmatch(match.group("value").strip())
                and not EXACT_EMPTY_VALUE.fullmatch(match.group("value").strip())
                for match in COOKIE_VALUE_PATTERN.finditer(cookie_array)
            ):
                return True
        return False

    @staticmethod
    def _matching_json_array_end(text: str, start: int) -> int | None:
        depth = 0
        quote_character: str | None = None
        escaped = False
        for index in range(start, len(text)):
            character = text[index]
            if quote_character is not None:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote_character:
                    quote_character = None
                continue
            if character in {'"', "'"}:
                quote_character = character
            elif character == "[":
                depth += 1
            elif character == "]":
                depth -= 1
                if depth == 0:
                    return index
        return None

    @staticmethod
    def _contains_escaped_keyed_secret(text: str) -> bool:
        cursor = 0
        while cursor < len(text):
            key_open = text.find('"', cursor)
            if key_open == -1:
                return False
            depth = SupportIntake._preceding_backslashes(text, key_open)
            if depth == 0 or depth % 2 == 0:
                cursor = key_open + 1
                continue
            key_close = SupportIntake._next_quote_at_depth(
                text, key_open + 1, depth
            )
            if key_close == -1:
                return False
            key = JSON_UNICODE_ESCAPE_PATTERN.sub(
                lambda escape: chr(int(escape.group("digits"), 16)),
                text[key_open + 1 : key_close - depth],
            )
            value_open = key_close + 1
            while value_open < len(text) and text[value_open].isspace():
                value_open += 1
            if value_open >= len(text) or text[value_open] not in ":=":
                cursor = key_close + 1
                continue
            value_open += 1
            while value_open < len(text) and text[value_open].isspace():
                value_open += 1
            value_quote = text.find('"', value_open)
            value_prefix = (
                text[value_open:value_quote] if value_quote != -1 else ""
            )
            value_depth = (
                SupportIntake._preceding_backslashes(text, value_quote)
                if value_quote != -1
                and value_prefix
                and set(value_prefix) == {"\\"}
                else 0
            )
            if not SECRET_KEY_NAME_PATTERN.fullmatch(key) or value_depth != depth:
                cursor = key_close + 1
                continue
            value_close = SupportIntake._next_quote_at_depth(
                text, value_quote + 1, depth
            )
            if value_close == -1:
                return True
            if (
                text[value_quote + 1 : value_close - depth].casefold()
                != "[redacted]"
            ):
                return True
            cursor = value_close + 1
        return False

    @staticmethod
    def _preceding_backslashes(text: str, quote_index: int) -> int:
        count = 0
        index = quote_index - 1
        while index >= 0 and text[index] == "\\":
            count += 1
            index -= 1
        return count

    @staticmethod
    def _next_quote_at_depth(text: str, start: int, depth: int) -> int:
        for index in range(start, len(text)):
            if (
                text[index] == '"'
                and SupportIntake._preceding_backslashes(text, index) == depth
            ):
                return index
        return -1

    @staticmethod
    def _contains_identifier(text: str) -> bool:
        decoded_variants, _exhausted = SupportIntake._html_decoded_variants(text)
        for candidate in decoded_variants:
            if any(pattern.search(candidate) for pattern in IDENTIFIER_PATTERNS):
                return True
            if any(
                pattern.search(candidate) for pattern in PREFIXED_IDENTIFIER_PATTERNS
            ):
                return True
            if SupportIntake._contains_escaped_keyed_identifier(candidate):
                return True
            if any(
                not EXACT_IDENTIFIER_VALUE.fullmatch(match.group("value").strip())
                and not EXACT_EMPTY_VALUE.fullmatch(match.group("value").strip())
                for match in KEYED_IDENTIFIER_PATTERN.finditer(candidate)
            ):
                return True
            if any(
                not EXACT_IDENTIFIER_VALUE.fullmatch(match.group("value").strip())
                and not EXACT_EMPTY_VALUE.fullmatch(match.group("value").strip())
                for match in LOG_DEVICE_IDENTIFIER_PATTERN.finditer(candidate)
            ):
                return True
            if any(
                not match.group("value").strip().isdigit()
                and not EXACT_IDENTIFIER_VALUE.fullmatch(match.group("value").strip())
                and not EXACT_EMPTY_VALUE.fullmatch(match.group("value").strip())
                for match in ADDRESS_IDENTIFIER_PATTERN.finditer(candidate)
            ):
                return True
            if any(
                VERSION_CONTEXT_PATTERN.search(
                    candidate[max(0, match.start() - 32) : match.start()]
                )
                is None
                for match in IPV4_PATTERN.finditer(candidate)
            ):
                return True
        return False

    @staticmethod
    def _identifier_label(key: str) -> str | None:
        normalized = re.sub(r"[^A-Za-z0-9]", "", key).casefold()
        if re.search(r"serial(?:number)?$", normalized):
            return "SERIAL"
        if re.search(
            r"(?:gatewayid|deviceid|devicesn|devicename|siteid|siteidentifier|"
            r"din|nmi|warpsitenumber|energysite)$",
            normalized,
        ):
            return "DEVICE"
        if re.search(
            r"(?:username|login|accountnumber|accountname|accountaddress|"
            r"siteaddress|concessionaddress|streetaddress|documentid|"
            r"emailaddress|invoicenumber|identifier)$",
            normalized,
        ):
            return "USER"
        return None

    @staticmethod
    def _contains_escaped_keyed_identifier(text: str) -> bool:
        cursor = 0
        while cursor < len(text):
            key_open = text.find('"', cursor)
            if key_open == -1:
                return False
            depth = SupportIntake._preceding_backslashes(text, key_open)
            if depth == 0 or depth % 2 == 0:
                cursor = key_open + 1
                continue
            key_close = SupportIntake._next_quote_at_depth(
                text, key_open + 1, depth
            )
            if key_close == -1:
                return False
            key = text[key_open + 1 : key_close - depth]
            value_open = key_close + 1
            while value_open < len(text) and text[value_open].isspace():
                value_open += 1
            if value_open >= len(text) or text[value_open] not in ":=":
                cursor = key_close + 1
                continue
            value_open += 1
            while value_open < len(text) and text[value_open].isspace():
                value_open += 1
            value_quote = text.find('"', value_open)
            value_prefix = text[value_open:value_quote] if value_quote != -1 else ""
            value_depth = (
                SupportIntake._preceding_backslashes(text, value_quote)
                if value_quote != -1
                and value_prefix
                and set(value_prefix) == {"\\"}
                else 0
            )
            if SupportIntake._identifier_label(key) is None or value_depth != depth:
                cursor = key_close + 1
                continue
            value_close = SupportIntake._next_quote_at_depth(
                text, value_quote + 1, depth
            )
            if value_close == -1:
                return True
            value = text[value_quote + 1 : value_close - depth]
            if not EXACT_IDENTIFIER_VALUE.fullmatch(value):
                return True
            cursor = value_close + 1
        return False

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
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
        except ValueError:
            return False
        return (
            parsed.scheme.casefold() == "https"
            and (hostname or "").casefold() == "github.com"
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
