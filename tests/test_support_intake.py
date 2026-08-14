from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from scripts.prepare_support_snapshot import SNAPSHOT_NAME, persist_snapshot
from scripts.revalidate_support_snapshot import revalidate_snapshot
from scripts.support_intake import (
    IntakeDecision,
    IntakeSnapshot,
    SupportIntake,
    snapshot_revision,
)


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
                "state": "open",
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
            {
                "ref": "main",
                "inputs": {
                    "issue_number": "42",
                    "evidence_revision": snapshot_revision(
                        SupportIntake(
                            client_for("Version 2.12.1000\nMonitoring mode: disabled")
                        ).evaluate("Plaintext-Lab/PowerSync", 42)
                    ),
                },
            },
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
        '{"password": "correct horse battery staple"}\n'
        "user@example.com connected from 192.168.1.10 using /home/alice/config"
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons
    assert "personal identifier in issue text" in decision.reasons
    assert not any("/dispatches" in path for _, path, _ in client.requests)


def test_escaped_quote_secret_fails_closed() -> None:
    client = client_for(r'{"password":"abc\"def ghi","next":"preserved"}')

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_serialized_json_credentials_fail_closed() -> None:
    client = client_for(
        r'{"payload":"{\"password\":\"hunter2\",\"status\":\"failed\"}"}'
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


@pytest.mark.parametrize(
    "evidence",
    (
        r'{"payload":"{\"Authorization\":\"Bearer super-secret-token\"}"}',
        '- password: "[REDACTED]"\n    correct horse\n    battery staple',
        '{"cookies":[{"name":"session","value":"live-session-secret"}]}',
        '{"push_tokens":{"ExponentPushToken[abcdefghijklmnopqrstuv]":{}}}',
        '{"push_tokens":{"abcdefghijklmnopqrstuv:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345":{}}}',
    ),
)
def test_additional_persisted_credentials_fail_closed(evidence: str) -> None:
    client = client_for(evidence)

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_html_encoded_credentials_fail_closed() -> None:
    client = client_for("{&quot;password&quot;&#58;&quot;hunter2&quot;}")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_redacted_serialized_json_credential_is_allowed() -> None:
    client = client_for(
        r'{"payload":"{\"password\":\"[REDACTED]\",\"status\":\"failed\"}"}'
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is True


def test_url_user_info_credentials_fail_closed() -> None:
    client = client_for("request http://admin:hunter2@192.168.1.10/api failed")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_url_without_user_info_is_allowed() -> None:
    client = client_for("request http://service.local/api failed")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is True


def test_deleted_comments_retrigger_deterministic_intake() -> None:
    workflow = Path(".github/workflows/support-intake.yml").read_text(
        encoding="utf-8"
    )

    assert "types: [created, edited, deleted]" in workflow


def test_multiword_username_fails_closed() -> None:
    client = client_for('{"username": "Alice Smith"}')

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "personal identifier in issue text" in decision.reasons


def test_redacted_credential_placeholder_is_allowed_in_a_marked_bundle() -> None:
    url = "https://github.com/user-attachments/files/123/powersync-support.log"
    client = client_for(f"[powersync-support.log]({url})")
    client.downloads[url] = (
        b'PowerSync sanitised support bundle v1\n{"password": "[REDACTED]"}\n'
        b"Cookie: [REDACTED]\n"
        b"Authorization: [REDACTED]\n"
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is True


def test_redacted_prefix_does_not_hide_a_credential_suffix() -> None:
    client = client_for('password="[REDACTED]actual-secret"')

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_short_and_fine_grained_credentials_fail_closed() -> None:
    client = client_for(
        "password=hunter2\ntoken=abc123\ngithub_pat_abcdefghijklmnopqrstuvwxyz"
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_standalone_power_sync_credentials_fail_closed() -> None:
    client = client_for(
        f"psk_abcdefghijklmnopqrstuvwxyz psync_{'a' * 43} xai-abcdefghijklmnopqrstuvwxyz "
        "AIzaabcdefghijklmnopqrstuvwxyz"
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_standalone_powersync_proxy_token_fails_closed() -> None:
    client = client_for(f"psync_{'a' * 43}")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_namespaced_integration_credentials_fail_closed() -> None:
    client = client_for(
        '{"alphaess_cloud_app_secret":"alpha-secret",'
        '"sigenergy_pass_enc":"encoded-pass",'
        '"teslemetry_api_token":"tesla-token"}'
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_all_repository_credential_key_suffixes_fail_closed() -> None:
    client = client_for(
        '{"foxess_cloud_password":"foxess-secret",'
        '"enphase_password":"enphase-secret",'
        '"openweathermap_api_key":"weather-secret",'
        '"powerwall_local_private_key_pem":"private-material"}'
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_quoted_json_authentication_headers_fail_closed() -> None:
    client = client_for(
        '{"Authorization":"Bearer super-secret-token",'
        '"Set-Cookie":"session=customer-secret"}'
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_every_authorization_header_value_fails_closed() -> None:
    client = client_for(
        '{"Authorization":"apikey customer-secret"}\nAuthorization: raw-token-value'
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_camel_case_credentials_fail_closed() -> None:
    client = client_for(
        '{"accessToken":"access-secret","refreshToken":"refresh-secret"}'
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_timezone_token_and_null_token_are_not_credentials() -> None:
    client = client_for('{"timezone_token":"AEST","token":null,"status":500}')

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is True


def test_quoted_power_sync_identifier_keys_fail_closed() -> None:
    client = client_for('{"serial_number": "TG123456789", "username": "alice"}')

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "personal identifier in issue text" in decision.reasons


def test_power_sync_site_and_gateway_identifiers_fail_closed() -> None:
    client = client_for(
        '{"gateway_id":"gateway-0123456789abcdef",'
        '"asset_site_id":"12345678-1234-1234-1234-123456789abc",'
        '"site_id":"01KAR0YMB7JQDVZ10SN1SGA0CV",'
        '"din":"DIN0123456789ABC"}'
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "personal identifier in issue text" in decision.reasons


def test_namespaced_and_customer_identifiers_fail_closed() -> None:
    client = client_for(
        '{"amber_site_id":"amber-site",'
        '"tesla_energy_site_id":"tesla-site",'
        '"sigenergy_device_id":"sig-device",'
        '"accountNumber":"customer-account",'
        '"siteAddress":"1 Main Street"}'
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "personal identifier in issue text" in decision.reasons


def test_repository_sensitive_identifier_keys_fail_closed() -> None:
    client = client_for(
        '{"nmi":"E1234567890","accountName":"Alice Smith",'
        '"siteIdentifier":"site-secret","device_sn":"device-secret"}'
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "personal identifier in issue text" in decision.reasons


def test_namespaced_camel_case_identifier_keys_fail_closed() -> None:
    client = client_for(
        '{"bmsSerialNumber":"BMS-123","packageSerialNumber":"PACK-456",'
        '"userDeviceId":"DEVICE-789"}'
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "personal identifier in issue text" in decision.reasons


def test_serialized_json_identifiers_fail_closed() -> None:
    client = client_for(
        r'{"payload":"{\"username\":\"Alice Smith\",\"device_id\":\"abc-123\"}"}'
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "personal identifier in issue text" in decision.reasons


def test_yaml_block_scalar_secret_fails_closed() -> None:
    client = client_for("password: |\n  correct horse\n  battery staple\nnext: preserved")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_redacted_yaml_header_with_raw_continuation_fails_closed() -> None:
    client = client_for('password: "[REDACTED]"\n  battery staple\nnext: preserved')

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "possible credential in issue text" in decision.reasons


def test_fully_redacted_yaml_continuation_is_allowed() -> None:
    client = client_for('password: "[REDACTED]"\n  [REDACTED]\nnext: preserved')

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is True


def test_numeric_modbus_address_is_not_treated_as_an_identifier() -> None:
    client = client_for('{"address":12345,"function":4}')

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is True


def test_device_display_names_fail_closed() -> None:
    client = client_for(
        '{"device_name":"Alice\'s iPhone"}\n'
        "Token entry - platform=ios, device=Alice's iPhone, "
        "registered_at=2026-08-13T10:00:00"
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "personal identifier in issue text" in decision.reasons


def test_pseudonymised_device_display_names_are_allowed() -> None:
    client = client_for(
        '{"device_name":"[DEVICE_1]"}\n'
        "Token entry - platform=ios, device=[DEVICE_1], "
        "registered_at=2026-08-13T10:00:00"
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is True


def test_dotted_mac_address_fails_closed() -> None:
    client = client_for("adapter aabb.ccdd.eeff disconnected")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "personal identifier in issue text" in decision.reasons


def test_power_sync_identifiers_in_log_phrases_fail_closed() -> None:
    client = client_for(
        "request for site 01KAR0YMB7JQDVZ10SN1SGA0CV energy_sites/1234567890123"
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "personal identifier in issue text" in decision.reasons


def test_four_part_version_is_not_misclassified_as_an_ip_address() -> None:
    client = client_for("integration version 1.2.3.4")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is True


def test_address_after_bare_integration_label_fails_closed() -> None:
    client = client_for("integration 192.168.1.10 failed")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "personal identifier in issue text" in decision.reasons


def test_closed_issue_fails_snapshot_revalidation() -> None:
    client = client_for("Version 2.12.1000")
    client.responses[("GET", "/repos/Plaintext-Lab/PowerSync/issues/42")]["state"] = (
        "closed"
    )

    snapshot = SupportIntake(client).evaluate("Plaintext-Lab/PowerSync", 42)

    assert snapshot.decision.safe is False
    assert "issue is not open" in snapshot.decision.reasons


def test_ipv6_identifier_fails_closed() -> None:
    client = client_for("Connected from 2001:db8:85a3::8a2e:370:7334")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "personal identifier in issue text" in decision.reasons


def test_embedded_ipv4_ipv6_and_lowercase_vin_fail_closed() -> None:
    client = client_for("::ffff:192.168.1.10 5yj3e1ea7nf0000a1")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "personal identifier in issue text" in decision.reasons


def test_windows_profile_names_containing_spaces_fail_closed() -> None:
    client = client_for(r"C:\Users\Alice Smith\AppData\powersync.log")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "personal identifier in issue text" in decision.reasons


def test_numeric_timestamp_is_not_classified_as_a_vin() -> None:
    client = client_for("event 20260813123456789")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is True


def test_sanitised_text_attachment_is_allowed() -> None:
    url = "https://github.com/user-attachments/files/123/powersync-support.log"
    client = client_for(f"[powersync-support.log]({url})")
    client.downloads[url] = (
        b"PowerSync sanitised support bundle v1\n"
        b"2026-08-13T10:00:00 INFO request completed\n"
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is True


def test_csv_attachment_is_rejected() -> None:
    url = "https://github.com/user-attachments/files/123/evidence.csv"
    client = client_for(f"[evidence.csv]({url})")
    client.downloads[url] = (
        b"PowerSync sanitised support bundle v1\nusername,password\nalice,secret\n"
    )

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert "attachment 1 is not an allowed text evidence format" in decision.reasons


def test_attachment_scheme_and_hostname_are_case_insensitive() -> None:
    url = "HTTPS://GitHub.com/user-attachments/files/123/raw.log"
    client = client_for(f"[raw.log]({url})")
    client.downloads[url] = b"ordinary unsanitised log"

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert (
        "attachment 1 is missing the sanitised support bundle marker"
        in decision.reasons
    )


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
    assert "attachment 2 is not an allowed text evidence format" in decision.reasons


def test_malformed_url_candidate_does_not_abort_intake() -> None:
    client = client_for("https://[broken/user-attachments/x.log")

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is True


def test_unmarked_or_binary_attachment_fails_closed() -> None:
    text_url = "https://github.com/user-attachments/files/123/raw.log"
    image_url = "https://github.com/user-attachments/assets/456/image.png"
    client = client_for(f"[raw.log]({text_url})\n![image]({image_url})")
    client.downloads[text_url] = b"ordinary unsanitised log"

    decision = SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert decision.safe is False
    assert (
        "attachment 1 is missing the sanitised support bundle marker"
        in decision.reasons
    )
    assert "attachment 2 is not an allowed text evidence format" in decision.reasons


def test_warning_does_not_persist_an_unsafe_attachment_name() -> None:
    url = "https://github.com/user-attachments/assets/456/image.png"
    client = client_for(f"![alice@example.com.png]({url})")

    SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    payload = client.requests[-1][2]
    assert payload is not None
    assert "alice@example.com" not in payload["body"]
    assert "attachment 1" in payload["body"]


def test_existing_warning_is_not_posted_twice() -> None:
    client = client_for(
        "password=super-secret-value",
        comments=[
            {
                "body": "<!-- powersync-intake:v1:unsafe -->\nExisting warning",
                "user": {"login": "github-actions[bot]"},
            }
        ],
    )

    SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert not any(
        path.endswith("/comments") and method == "POST"
        for method, path, _ in client.requests
    )


def test_reporter_cannot_spoof_an_existing_warning() -> None:
    client = client_for(
        "password=super-secret-value",
        comments=[
            {
                "body": "<!-- powersync-intake:v1:unsafe -->\nCopied warning",
                "user": {"login": "reporter"},
            }
        ],
    )

    SupportIntake(client).inspect("Plaintext-Lab/PowerSync", 42)

    assert any(
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
    safe_outputs = tmp_path / "missing" / "outputs.jsonl"
    snapshot = IntakeSnapshot(
        decision=IntakeDecision(safe=False, reasons=("unsafe",)),
        content="unsafe evidence",
        labels=frozenset({"unsafe evidence"}),
    )

    assert persist_snapshot(snapshot, safe_outputs, tmp_path) is False
    assert not (tmp_path / SNAPSHOT_NAME).exists()
    assert json.loads(safe_outputs.read_text(encoding="utf-8"))["type"] == "noop"


def test_snapshot_revalidation_rejects_changed_evidence() -> None:
    original_client = client_for("Version 2.12.1000")
    original = SupportIntake(original_client).evaluate("Plaintext-Lab/PowerSync", 42)
    changed_client = client_for("Version 2.12.1001")

    assert (
        revalidate_snapshot(
            changed_client,
            "Plaintext-Lab/PowerSync",
            42,
            snapshot_revision(original),
        )
        is False
    )


def test_snapshot_revalidation_accepts_same_safe_revision() -> None:
    client = client_for("Version 2.12.1000")
    original = SupportIntake(client).evaluate("Plaintext-Lab/PowerSync", 42)
    current_client = client_for("Version 2.12.1000")
    current_client.responses[("GET", "/repos/Plaintext-Lab/PowerSync/issues/42")][
        "labels"
    ] = [{"name": "needs triage"}, {"name": "safe evidence"}]

    assert (
        revalidate_snapshot(
            current_client,
            "Plaintext-Lab/PowerSync",
            42,
            snapshot_revision(original),
        )
        is True
    )


def test_snapshot_revalidation_rejects_changed_classification_labels() -> None:
    original_client = client_for("Version 2.12.1000")
    original = SupportIntake(original_client).evaluate("Plaintext-Lab/PowerSync", 42)
    changed_client = client_for("Version 2.12.1000")
    changed_client.responses[("GET", "/repos/Plaintext-Lab/PowerSync/issues/42")][
        "labels"
    ] = [{"name": "enhancement"}, {"name": "safe evidence"}]

    assert (
        revalidate_snapshot(
            changed_client,
            "Plaintext-Lab/PowerSync",
            42,
            snapshot_revision(original),
        )
        is False
    )


def test_json_nesting_ignores_braces_inside_strings() -> None:
    content = '{"message":"' + ("{" * 25) + '","status":500}'

    assert SupportIntake._excessively_nested(content, ".json") is False


def test_triage_passes_the_compiler_safe_output_path_to_snapshot_capture() -> None:
    workflow = Path(".github/workflows/issue-triage.md").read_text(encoding="utf-8")

    assert (
        "GH_AW_SAFE_OUTPUTS: "
        "${{ steps.set-runtime-paths.outputs.GH_AW_SAFE_OUTPUTS }}" in workflow
    )
    assert "toolsets: [repos]" in workflow
    assert "group: issue-triage-${{ inputs.issue_number }}" in workflow
    assert (
        "remove `needs triage`, `needs information`, and `needs investigation`"
        in workflow
    )
    assert (
        "SUPPORT_EVIDENCE_REVISION: ${{ github.event.inputs.evidence_revision }}"
        in workflow
    )
    assert "python -m scripts.revalidate_support_snapshot" in workflow
    assert workflow.count("target: ${{ github.event.inputs.issue_number }}") == 3
