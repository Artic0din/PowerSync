from __future__ import annotations

from typing import Any

import pytest

from scripts.read_support_bundle import fetch_support_bundle, validate_support_bundle


class FakeResponse:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self, maximum_bytes: int) -> bytes:
        return self._content[:maximum_bytes]


def test_valid_sanitised_bundle_is_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = (
        b"PowerSync sanitised support bundle v1\n"
        b"2026-08-13T10:00:00 ERROR request failed\n"
    )
    monkeypatch.setattr(
        "scripts.read_support_bundle.urlopen",
        lambda *_args, **_kwargs: FakeResponse(content),
    )

    result = fetch_support_bundle(
        "https://github.com/user-attachments/files/123/powersync-support.log"
    )

    assert result == content.decode()


def test_non_attachment_url_is_rejected_before_download() -> None:
    with pytest.raises(ValueError, match="GitHub user attachment"):
        fetch_support_bundle("https://example.com/customer.log")


def test_bundle_is_rescanned_before_agent_output() -> None:
    with pytest.raises(ValueError, match="possible credential"):
        validate_support_bundle(
            b"PowerSync sanitised support bundle v1\n"
            b"Cookie: session=customer-session-value"
        )
