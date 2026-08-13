from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.prepare_support_snapshot import SNAPSHOT_NAME, persist_snapshot
from scripts.support_intake import IntakeDecision, IntakeSnapshot, SupportIntake


@dataclass
class FakeGitHubClient:
    responses: dict[tuple[str, str], Any] = field(default_factory=dict)
    downloads: dict[str, bytes] = field(default_factory=dict)
    requests: list[tuple[str, str, dict[str, Any] | None]] = field(default_factory=list)

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        self.requests.append((method, path, payload))
        return self.responses.get((method, path), {})

    def download(self, url: str, maximum_bytes: int) -> bytes:
        content = self.downloads[url]
        if len(content) > maximum_bytes:
            raise ValueError("Attachment exceeds the support evidence size limit")
        return content


def client_for(
    body: str, *, comments: list[dict[str, Any]] | None = None
) -> FakeGitHubClient:
    return FakeGitHubClient(
        responses={
            ("GET", "/repos/Plaintext-Lab/PowerSync/issues/42"): {
                "number": 42,
                "title": "PowerSync problem",
                "body": body,
                "labels": [{"name": "needs triage"}],
            },
            (
                "GET",
                "/repos/Plaintext-Lab/PowerSync/issues/42/comments?per_page=100&page=1",
            ): comments or [],
        }
    )


def test_clean_text_dispatches_triage() -> None:
    client = client_for("Version 2.12.1000\nMonitoring mode: disabled")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision == IntakeDecision(safe=True, reasons=())
    assert client.requests[-3:] == [
        (
            "POST",
            "/repos/Plaintext-Lab/PowerSync/issues/42/labels",
            {"labels": ["safe evidence"]},
        ),
        (
            "DELETE",
            "/repos/Plaintext-Lab/PowerSync/issues/42/labels/unsafe%20evidence",
            None,
        ),
        (
            "POST",
            "/repos/Plaintext-Lab/PowerSync/actions/workflows/issue-triage.lock.yml/dispatches",
            {"ref": "main", "inputs": {"issue_number": "42"}},
        ),
    ]


def test_secret_pattern_fails_closed_without_dispatch() -> None:
    client = client_for("Authorization: Bearer secret-value-1234567890")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons
    assert not any("/dispatches" in path for _, path, _ in client.requests)
    assert client.requests[-1][0:2] == (
        "POST",
        "/repos/Plaintext-Lab/PowerSync/issues/42/comments",
    )
    payload = client.requests[-1][2]
    assert payload is not None
    assert "revoke or rotate it" in payload["body"]


def test_cookie_and_generic_token_patterns_fail_closed() -> None:
    client = client_for(
        "Cookie: session=customer-session-value\n"
        "refresh_token=customer-refresh-token-value"
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons
    assert not any("/dispatches" in path for _, path, _ in client.requests)


def test_quoted_json_secret_and_inline_identifiers_fail_closed() -> None:
    client = client_for(
        '{"password": "super-secret-value"}\n'
        "user@example.com connected from 192.168.1.10 using /home/alice/config"
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons
    assert "personal identifier in issue text" in decision.reasons
    assert not any("/dispatches" in path for _, path, _ in client.requests)


def test_sanitised_text_attachment_is_allowed() -> None:
    url = "https://github.com/user-attachments/files/123/powersync-support.log"
    client = client_for(f"[powersync-support.log]({url})")
    client.downloads[url] = (
        b"PowerSync sanitised support bundle v1\n"
        b"2026-08-13T10:00:00 INFO request completed\n"
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is True


def test_evaluate_captures_the_exact_safe_evidence_without_mutating() -> None:
    url = "https://github.com/user-attachments/files/123/powersync-support.log"
    client = client_for(f"[powersync-support.log]({url})")
    client.responses[("GET", "/repos/Plaintext-Lab/PowerSync/issues/42")]["user"] = {
        "login": "reporter"
    }
    client.downloads[url] = (
        b"PowerSync sanitised support bundle v1\n2026-08-13 INFO captured\n"
    )

    snapshot = SupportIntake(client).evaluate("Plaintext-Lab/PowerSync", 42)

    assert snapshot.decision.safe is True
    assert "Author: reporter" in snapshot.content
    assert "2026-08-13 INFO captured" in snapshot.content
    assert all(method == "GET" for method, _, _ in client.requests)


def test_bare_and_html_attachment_urls_are_inspected() -> None:
    bare_url = "https://github.com/user-attachments/files/123/powersync-support.log"
    html_url = "https://github.com/user-attachments/assets/456/image.png"
    client = client_for(f'{bare_url}\n<img src="{html_url}">')
    client.downloads[bare_url] = (
        b"PowerSync sanitised support bundle v1\n2026-08-13 INFO ok\n"
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "image.png is not an allowed text evidence format" in decision.reasons


def test_unmarked_or_binary_attachment_fails_closed() -> None:
    text_url = "https://github.com/user-attachments/files/123/raw.log"
    image_url = "https://github.com/user-attachments/assets/456/image.png"
    client = client_for(f"[raw.log]({text_url})\n![image]({image_url})")
    client.downloads[text_url] = b"ordinary unsanitised log"

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "raw.log is missing the sanitised support bundle marker" in decision.reasons
    assert "image.png is not an allowed text evidence format" in decision.reasons


def test_existing_warning_is_not_posted_twice() -> None:
    client = client_for(
        "password=super-secret-value",
        comments=[{"body": "<!-- powersync-intake:v1:unsafe -->\nExisting warning"}],
    )

    SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert not any(
        path.endswith("/comments") and method == "POST"
        for method, path, _ in client.requests
    )


def test_safe_snapshot_is_persisted_and_excluded(tmp_path: Path) -> None:
    (tmp_path / ".git" / "info").mkdir(parents=True)
    (tmp_path / ".git" / "info" / "exclude").write_text("", encoding="utf-8")
    safe_outputs = tmp_path / "outputs.jsonl"
    snapshot = IntakeSnapshot(
        decision=IntakeDecision(safe=True, reasons=()),
        content="captured evidence",
        labels=frozenset({"safe evidence"}),
    )

    assert persist_snapshot(snapshot, safe_outputs, tmp_path) is True
    assert (tmp_path / SNAPSHOT_NAME).read_text(encoding="utf-8") == "captured evidence"
    assert f"/{SNAPSHOT_NAME}" in (tmp_path / ".git" / "info" / "exclude").read_text(
        encoding="utf-8"
    )


def test_unsafe_snapshot_noops_without_writing_evidence(tmp_path: Path) -> None:
    safe_outputs = tmp_path / "outputs.jsonl"
    snapshot = IntakeSnapshot(
        decision=IntakeDecision(safe=False, reasons=("unsafe",)),
        content="unsafe evidence",
        labels=frozenset({"unsafe evidence"}),
    )

    assert persist_snapshot(snapshot, safe_outputs, tmp_path) is False
    assert not (tmp_path / SNAPSHOT_NAME).exists()
    assert json.loads(safe_outputs.read_text(encoding="utf-8"))["type"] == "noop"
