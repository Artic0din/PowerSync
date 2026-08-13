"""Read one intake-approved GitHub support bundle for an agent workflow."""

from __future__ import annotations

import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scripts.support_intake import (
    MAX_ATTACHMENT_BYTES,
    SANITISED_MARKER,
    SECRET_PATTERNS,
)

ATTACHMENT_URL = re.compile(
    r"\Ahttps://github\.com/user-attachments/(?:files|assets)/"
    r"[A-Za-z0-9._~!$&'()*+,;=:@%/-]+\Z"
)


def validate_support_bundle(content: bytes) -> str:
    """Fail closed unless content still satisfies the deterministic intake contract."""
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError("attachment exceeds the support evidence size limit")
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("attachment is not UTF-8 text") from error
    if "\x00" in decoded:
        raise ValueError("attachment contains binary data")
    if not decoded.startswith(SANITISED_MARKER):
        raise ValueError("attachment is missing the sanitised support bundle marker")
    if any(pattern.search(decoded) for pattern in SECRET_PATTERNS):
        raise ValueError("attachment still contains a possible credential")
    return decoded


def fetch_support_bundle(url: str) -> str:
    """Download and revalidate one public GitHub user attachment."""
    if ATTACHMENT_URL.fullmatch(url) is None:
        raise ValueError("evidence URL is not a GitHub user attachment")
    request = Request(url, headers={"User-Agent": "PowerSync-support-reader/1"})
    try:
        with urlopen(request, timeout=30) as response:
            content = response.read(MAX_ATTACHMENT_BYTES + 1)
    except (HTTPError, URLError) as error:
        raise ValueError("attachment could not be downloaded") from error
    return validate_support_bundle(content)


def main() -> int:
    if len(sys.argv) != 2:
        raise ValueError("usage: python -m scripts.read_support_bundle URL")
    print(fetch_support_bundle(sys.argv[1]), end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(f"support bundle rejected: {error}", file=sys.stderr)
        raise SystemExit(1) from error
