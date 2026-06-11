"""Extended tests for powerwall_local.dispatch.

Covers paths not exercised by the existing test_dispatch.py:

    - asyncio.TimeoutError is treated as a fallback exception (cloud runs)
    - PowerwallAuthError is treated as a fallback exception (cloud runs)
    - PowerwallLocalError (base class) is treated as a fallback exception
    - retry_local_once=True with first-attempt failure but second-attempt success
      (cloud should NOT run)
    - get_local_transport returns None when transport is not a TEDAPIv1rTransport
      instance (wrong type stored in runtime bucket)
    - dispatch returns cloud result when entry has no runtime data at all
    - timeout parameter is forwarded to asyncio.wait_for

Run with:
    pytest tests/test_dispatch_extended.py
    pytest tests/test_dispatch.py tests/test_dispatch_extended.py  (order-safe)

Module-isolation strategy
--------------------------
test_dispatch.py registers its own HA + power_sync stubs at module-load time
using both ``sys.modules.setdefault`` and direct ``sys.modules[...] = ...``
assignments, then imports the dispatch module. This file must stay compatible
with either load order:

    A) This file loads first:  register stubs, import dispatch, test_dispatch.py
       runs and overwrites some stubs but dispatch is already cached with OUR
       classes — our tests are fine; test_dispatch.py reuses our cached dispatch.

    B) test_dispatch.py loads first:  dispatch is already cached with ITS classes
       — we must read class objects back from the already-imported dispatch module
       rather than registering our own.

The key insight: the dispatch module's global namespace always has the canonical
class objects (TEDAPIv1rTransport, PowerwallLocalError, etc.) it was imported
with. We inspect those in the module-level setup and alias them here — regardless
of load order, our isinstance-checked transport and our side_effect exceptions
always match what the live dispatch module will test/catch.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "power_sync"

DISPATCH_MOD_NAME = "power_sync.powerwall_local.dispatch"


def _bootstrap_stubs() -> None:
    """Register stub modules for symbols dispatch.py needs at import time.

    Uses setdefault so that if test_dispatch.py ran first and already registered
    real stubs, we leave those intact.  If we run first we provide minimal stubs
    sufficient for the dispatch module to import without the HA/aiohttp runtime.
    """
    if "homeassistant" not in sys.modules:
        ha_root = types.ModuleType("homeassistant")
        ha_cfg = types.ModuleType("homeassistant.config_entries")
        ha_core = types.ModuleType("homeassistant.core")
        ha_cfg.ConfigEntry = type("ConfigEntry", (), {})
        ha_core.HomeAssistant = type("HomeAssistant", (), {})
        sys.modules.setdefault("homeassistant", ha_root)
        sys.modules.setdefault("homeassistant.config_entries", ha_cfg)
        sys.modules.setdefault("homeassistant.core", ha_core)

    if "power_sync" not in sys.modules:
        ps = types.ModuleType("power_sync")
        ps.__path__ = [str(ROOT)]
        sys.modules.setdefault("power_sync", ps)

    if "power_sync.powerwall_local" not in sys.modules:
        pwl = types.ModuleType("power_sync.powerwall_local")
        pwl.__path__ = [str(ROOT / "powerwall_local")]
        sys.modules.setdefault("power_sync.powerwall_local", pwl)

    if "power_sync.const" not in sys.modules:
        const = types.ModuleType("power_sync.const")
        const.CONF_POWERWALL_LOCAL_IP = "powerwall_local_ip"
        const.CONF_POWERWALL_LOCAL_PAIRED = "powerwall_local_paired"
        const.DOMAIN = "power_sync"
        sys.modules.setdefault("power_sync.const", const)

    if "power_sync.powerwall_local.exceptions" not in sys.modules:
        exc = types.ModuleType("power_sync.powerwall_local.exceptions")

        class PowerwallLocalError(Exception): ...
        class PowerwallAuthError(PowerwallLocalError): ...
        class PowerwallUnreachableError(PowerwallLocalError): ...
        class PowerwallSignatureError(PowerwallLocalError): ...

        exc.PowerwallLocalError = PowerwallLocalError
        exc.PowerwallAuthError = PowerwallAuthError
        exc.PowerwallUnreachableError = PowerwallUnreachableError
        exc.PowerwallSignatureError = PowerwallSignatureError
        sys.modules.setdefault("power_sync.powerwall_local.exceptions", exc)

    if "power_sync.powerwall_local.transport" not in sys.modules:
        tr = types.ModuleType("power_sync.powerwall_local.transport")

        class TEDAPIv1rTransport: ...

        tr.TEDAPIv1rTransport = TEDAPIv1rTransport
        sys.modules.setdefault("power_sync.powerwall_local.transport", tr)


_bootstrap_stubs()

# Import or retrieve the dispatch module.  importlib.import_module returns the
# cached copy when already present — it does NOT re-execute the module body.
_dispatch_mod = importlib.import_module(DISPATCH_MOD_NAME)

# After we have our own reference to the dispatch module we evict it from the
# module cache.  This allows test_dispatch.py (which loads after us in ORDER2)
# to perform a fresh import using its own stub registrations, so its
# TEDAPIv1rTransport and exception classes end up baked into its copy of the
# dispatch module.  Our copy (_dispatch_mod) remains valid because Python
# module objects are just plain objects — the cache entry is separate from the
# object reference.
sys.modules.pop(DISPATCH_MOD_NAME, None)

# Grab the canonical class objects that the *live* dispatch module was compiled
# with.  This handles both load orders correctly:
#   * We loaded first  → classes come from our stubs (set via setdefault above)
#   * test_dispatch.py loaded first → classes come from its stubs (force-set)
# Either way _dispatch_mod.TEDAPIv1rTransport is what get_local_transport uses.
TEDAPIv1rTransport = _dispatch_mod.TEDAPIv1rTransport  # type: ignore[attr-defined]
PowerwallLocalError = _dispatch_mod.PowerwallLocalError  # type: ignore[attr-defined]
PowerwallAuthError = _dispatch_mod.PowerwallAuthError  # type: ignore[attr-defined]
PowerwallUnreachableError = _dispatch_mod.PowerwallUnreachableError  # type: ignore[attr-defined]
PowerwallSignatureError = _dispatch_mod.PowerwallSignatureError  # type: ignore[attr-defined]

dispatch_powerwall_write = _dispatch_mod.dispatch_powerwall_write
get_local_transport = _dispatch_mod.get_local_transport


# ----- Helpers ----------------------------------------------------------------

class _FakeConfigEntry:
    def __init__(self, entry_id: str = "abc", data: dict | None = None):
        self.entry_id = entry_id
        self.data = dict(data or {})


def _hass_with_transport(transport: object) -> MagicMock:
    hass = MagicMock()
    hass.data = {
        "power_sync": {
            "abc": {"powerwall_local": {"client": MagicMock(_transport=transport)}}
        }
    }
    return hass


def _paired(entry_id: str = "abc") -> _FakeConfigEntry:
    return _FakeConfigEntry(
        entry_id=entry_id,
        data={"powerwall_local_paired": True, "powerwall_local_ip": "192.168.1.50"},
    )


# ---------------------------------------------------------------------------
# Timeout and auth-error fallback paths
# ---------------------------------------------------------------------------

def test_asyncio_timeout_error_triggers_cloud_fallback():
    """asyncio.TimeoutError from local call falls back to cloud."""

    async def _run():
        # ARRANGE
        local = AsyncMock(side_effect=asyncio.TimeoutError())
        cloud = AsyncMock(return_value="cloud-result")
        hass = _hass_with_transport(TEDAPIv1rTransport())
        # ACT
        result = await dispatch_powerwall_write(
            hass, _paired(),
            local_call=local, cloud_call=cloud, label="timeout_test",
            retry_local_once=False,
        )
        # ASSERT
        assert result == "cloud-result"
        assert cloud.await_count == 1

    asyncio.run(_run())


def test_powerwall_auth_error_triggers_cloud_fallback():
    """PowerwallAuthError is a listed fallback exception — cloud runs."""

    async def _run():
        # ARRANGE
        local = AsyncMock(side_effect=PowerwallAuthError("401 Unauthorized"))
        cloud = AsyncMock(return_value="cloud-auth-ok")
        hass = _hass_with_transport(TEDAPIv1rTransport())
        # ACT
        result = await dispatch_powerwall_write(
            hass, _paired(),
            local_call=local, cloud_call=cloud, label="auth_test",
            retry_local_once=False,
        )
        # ASSERT
        assert result == "cloud-auth-ok"
        assert local.await_count == 1

    asyncio.run(_run())


def test_powerwall_local_error_base_triggers_cloud_fallback():
    """PowerwallLocalError (base class) is also a listed fallback exception."""

    async def _run():
        # ARRANGE
        local = AsyncMock(side_effect=PowerwallLocalError("generic local error"))
        cloud = AsyncMock(return_value="cloud-base-ok")
        hass = _hass_with_transport(TEDAPIv1rTransport())
        # ACT
        result = await dispatch_powerwall_write(
            hass, _paired(),
            local_call=local, cloud_call=cloud, label="base_err_test",
            retry_local_once=False,
        )
        # ASSERT
        assert result == "cloud-base-ok"
        assert cloud.await_count == 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Retry-once path
# ---------------------------------------------------------------------------

def test_first_attempt_timeout_second_attempt_success_skips_cloud():
    """First local attempt times out, second attempt succeeds — cloud not called."""

    async def _run():
        # ARRANGE
        call_count = {"n": 0}

        async def local_call(transport):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise asyncio.TimeoutError()
            return True

        cloud = AsyncMock(return_value="cloud-should-not-run")
        hass = _hass_with_transport(TEDAPIv1rTransport())
        # ACT
        result = await dispatch_powerwall_write(
            hass, _paired(),
            local_call=local_call, cloud_call=cloud, label="retry_success",
            retry_local_once=True,
        )
        # ASSERT
        assert result is True
        assert call_count["n"] == 2
        assert cloud.await_count == 0

    asyncio.run(_run())


def test_retry_local_once_false_does_not_retry_on_failure():
    """retry_local_once=False means exactly one local attempt before cloud."""

    async def _run():
        # ARRANGE
        local = AsyncMock(side_effect=PowerwallUnreachableError("down"))
        cloud = AsyncMock(return_value="cloud-only")
        hass = _hass_with_transport(TEDAPIv1rTransport())
        # ACT
        result = await dispatch_powerwall_write(
            hass, _paired(),
            local_call=local, cloud_call=cloud, label="no_retry",
            retry_local_once=False,
        )
        # ASSERT: local tried exactly once, cloud ran once
        assert result == "cloud-only"
        assert local.await_count == 1
        assert cloud.await_count == 1

    asyncio.run(_run())


def test_falsy_local_first_attempt_retried_exactly_once():
    """A falsy local result on both retries falls back to cloud."""

    async def _run():
        # ARRANGE: always returns False (hash mismatch scenario)
        local = AsyncMock(return_value=False)
        cloud = AsyncMock(return_value="cloud-fallback")
        hass = _hass_with_transport(TEDAPIv1rTransport())
        # ACT
        result = await dispatch_powerwall_write(
            hass, _paired(),
            local_call=local, cloud_call=cloud, label="falsy_retry",
            retry_local_once=True,
        )
        # ASSERT: two local attempts, one cloud attempt
        assert result == "cloud-fallback"
        assert local.await_count == 2
        assert cloud.await_count == 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# get_local_transport edge cases
# ---------------------------------------------------------------------------

def test_get_local_transport_returns_none_for_wrong_transport_type():
    """If the stored transport is not a TEDAPIv1rTransport, return None."""
    # ARRANGE: client exists but _transport is a plain object (e.g. PW2 path)
    hass = MagicMock()
    wrong_transport = object()  # not a TEDAPIv1rTransport
    hass.data = {
        "power_sync": {
            "abc": {"powerwall_local": {"client": MagicMock(_transport=wrong_transport)}}
        }
    }
    entry = _paired()
    # ACT
    result = get_local_transport(hass, entry)
    # ASSERT
    assert result is None


def test_get_local_transport_returns_none_when_no_powerwall_local_key():
    """Runtime bucket exists but 'powerwall_local' key is absent."""
    # ARRANGE
    hass = MagicMock()
    hass.data = {"power_sync": {"abc": {}}}
    entry = _paired()
    # ACT
    result = get_local_transport(hass, entry)
    # ASSERT
    assert result is None


def test_get_local_transport_returns_none_when_entry_id_absent():
    """Entry exists in hass.data domain but this specific entry_id is not there."""
    # ARRANGE
    hass = MagicMock()
    hass.data = {"power_sync": {"other-entry": {}}}
    entry = _paired()  # entry_id = "abc"
    # ACT
    result = get_local_transport(hass, entry)
    # ASSERT
    assert result is None


def test_dispatch_falls_back_to_cloud_when_no_domain_data():
    """hass.data has no 'power_sync' key at all — cloud runs."""

    async def _run():
        # ARRANGE
        hass = MagicMock()
        hass.data = {}
        local = AsyncMock(return_value=True)
        cloud = AsyncMock(return_value="cloud-no-domain")
        # ACT
        result = await dispatch_powerwall_write(
            hass, _paired(),
            local_call=local, cloud_call=cloud, label="no_domain",
        )
        # ASSERT
        assert result == "cloud-no-domain"
        assert local.await_count == 0
        assert cloud.await_count == 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Timeout parameter forwarding
# ---------------------------------------------------------------------------

def test_custom_timeout_is_forwarded_to_wait_for():
    """The timeout kwarg is passed through to asyncio.wait_for."""

    async def _run():
        # ARRANGE: capture the timeout asyncio.wait_for receives
        captured: dict = {}
        original_wait_for = asyncio.wait_for

        async def fake_wait_for(coro, timeout):
            captured["timeout"] = timeout
            return await original_wait_for(coro, timeout=60.0)

        local = AsyncMock(return_value=True)
        cloud = AsyncMock(return_value="cloud-never")
        hass = _hass_with_transport(TEDAPIv1rTransport())

        with patch(DISPATCH_MOD_NAME + ".asyncio.wait_for", fake_wait_for):
            await dispatch_powerwall_write(
                hass, _paired(),
                local_call=local, cloud_call=cloud, label="timeout_param",
                timeout=3.5,
            )
        return captured

    captured = asyncio.run(_run())
    # ASSERT
    assert abs(captured["timeout"] - 3.5) < 0.001


def test_default_timeout_is_five_seconds():
    """When timeout is not specified the default 5.0 seconds is used."""

    async def _run():
        # ARRANGE
        captured: dict = {}
        original_wait_for = asyncio.wait_for

        async def fake_wait_for(coro, timeout):
            captured["timeout"] = timeout
            return await original_wait_for(coro, timeout=60.0)

        local = AsyncMock(return_value=True)
        cloud = AsyncMock(return_value="cloud-never")
        hass = _hass_with_transport(TEDAPIv1rTransport())

        with patch(DISPATCH_MOD_NAME + ".asyncio.wait_for", fake_wait_for):
            # ACT: no timeout kwarg — should use default 5.0
            await dispatch_powerwall_write(
                hass, _paired(),
                local_call=local, cloud_call=cloud, label="default_timeout",
            )
        return captured

    captured = asyncio.run(_run())
    # ASSERT
    assert abs(captured["timeout"] - 5.0) < 0.001
