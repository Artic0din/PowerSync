"""Tests for Powerwall local pairing Fleet API payloads."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


ROOT = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "power_sync"
    / "powerwall_local"
)


def _load_pairing_module():
    pkg = types.ModuleType("power_sync")
    pkg.__path__ = [str(ROOT.parent)]
    sys.modules.setdefault("power_sync", pkg)

    local_pkg = types.ModuleType("power_sync.powerwall_local")
    local_pkg.__path__ = [str(ROOT)]
    sys.modules.setdefault("power_sync.powerwall_local", local_pkg)

    exc_spec = importlib.util.spec_from_file_location(
        "power_sync.powerwall_local.exceptions", ROOT / "exceptions.py"
    )
    assert exc_spec is not None and exc_spec.loader is not None
    exceptions = importlib.util.module_from_spec(exc_spec)
    sys.modules["power_sync.powerwall_local.exceptions"] = exceptions
    exc_spec.loader.exec_module(exceptions)

    spec = importlib.util.spec_from_file_location(
        "power_sync.powerwall_local.pairing", ROOT / "pairing.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["power_sync.powerwall_local.pairing"] = module
    spec.loader.exec_module(module)
    return module


pairing = _load_pairing_module()


def test_authorization_command_payload_includes_fleet_api_metadata():
    payload = pairing._authorization_command_payload(
        "add_authorized_client_request",
        {"add_authorized_client_request": {"public_key": "abc"}},
    )

    assert payload["category"] == "authorization"
    assert payload["command_name"] == "add_authorized_client_request"
    assert payload["command_type"] == "grpc_command"
    assert payload["command_properties"] == {
        "message": {
            "authorization": {
                "add_authorized_client_request": {"public_key": "abc"}
            }
        },
        "identifier_type": 1,
    }


def test_list_authorized_clients_payload_uses_same_envelope():
    payload = pairing._authorization_command_payload(
        "list_authorized_clients_request",
        {"list_authorized_clients_request": {}},
    )

    assert payload["category"] == "authorization"
    assert payload["command_name"] == "list_authorized_clients_request"
    assert payload["command_properties"]["message"]["authorization"] == {
        "list_authorized_clients_request": {}
    }


def test_pairing_success_schedules_local_warmup_without_blocking():
    views_source = (ROOT / "views.py").read_text()
    button_source = (ROOT.parent / "button.py").read_text()

    # Background task (not async_create_task) so a slow first poll on an
    # unreachable gateway can never stall HA bootstrap.
    assert "hass.async_create_background_task(" in views_source
    assert "_warm_powerwall_local_coordinator(hass, entry)" in views_source
    assert "_schedule_local_coordinator_warmup(self._hass, entry)" in views_source
    assert "_schedule_local_coordinator_warmup(hass, entry)" in button_source
