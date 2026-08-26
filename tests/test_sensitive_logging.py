"""Tests for shared log redaction helpers."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import io
import logging
from pathlib import Path
import sys
import textwrap
import types


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "power_sync"
    / "sensitive_logging.py"
)
_SPEC = importlib.util.spec_from_file_location("power_sync_sensitive_logging", _MODULE_PATH)
assert _SPEC is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)
obfuscate_vin_tokens = _MODULE.obfuscate_vin_tokens
obfuscate_log_arg = _MODULE.obfuscate_log_arg


VIN = "LRWYHCFS3PC901374"
MASKED_VIN = "LRWY*********1374"


def _mask(value: str) -> str:
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def test_obfuscate_vin_tokens_masks_bare_vin_contexts() -> None:
    text = (
        "Auto-schedule status: {'LRWYHCFS3PC901374': False}; "
        "Multi-vehicle decision for Keksla (LRWYHCFS3PC901374); "
        "EV Coordinator: Vehicle LRWYHCFS3PC901374"
    )

    result = obfuscate_vin_tokens(text, _mask)

    assert VIN not in result
    assert result.count(MASKED_VIN) == 3


def test_obfuscate_vin_tokens_leaves_already_masked_values() -> None:
    text = "ChargingScheduleView: VIN LRWY*********1374"

    assert obfuscate_vin_tokens(text, _mask) == text


def test_obfuscate_vin_tokens_ignores_non_vin_tokens() -> None:
    text = "site 12345678901234567 and token ABCDEFGHJKLMNPRST"

    assert obfuscate_vin_tokens(text, _mask) == text


def test_obfuscate_log_arg_preserves_non_string_types() -> None:
    assert obfuscate_log_arg(4.939078848884e-05, _mask) == 4.939078848884e-05
    assert obfuscate_log_arg(42, _mask) == 42
    assert obfuscate_log_arg(True, _mask) is True


def test_expo_push_logging_never_contains_registered_token() -> None:
    """A diagnostic log must not disclose the credential used to send pushes."""
    actions_path = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "power_sync"
        / "automations"
        / "actions.py"
    )
    source = actions_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_send_expo_push"
    )
    segment = ast.get_source_segment(source, function)
    assert segment is not None

    package_name = "powersync_push_logging_testpkg"
    package = types.ModuleType(package_name)
    package.__path__ = []
    automations = types.ModuleType(f"{package_name}.automations")
    automations.__path__ = []
    const = types.ModuleType(f"{package_name}.const")
    const.DOMAIN = "power_sync"
    sys.modules[package_name] = package
    sys.modules[f"{package_name}.automations"] = automations
    sys.modules[f"{package_name}.const"] = const

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def text(self):
            return '{"data":[{"status":"ok","id":"receipt"}]}'

        async def json(self):
            return {"data": [{"status": "ok", "id": "receipt"}]}

    class ClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            return Response()

    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientSession = ClientSession
    original_aiohttp = sys.modules.get("aiohttp")
    sys.modules["aiohttp"] = aiohttp

    stream = io.StringIO()
    logger = logging.getLogger("powersync.push_logging.regression")
    handler = logging.StreamHandler(stream)
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    namespace = {
        "__name__": f"{package_name}.automations.actions",
        "__package__": f"{package_name}.automations",
        "HomeAssistant": object,
        "_LOGGER": logger,
    }
    exec(textwrap.dedent(segment), namespace)

    token = "ExponentPushToken[secret-ticket-345-token]"
    hass = types.SimpleNamespace(
        data={
            "power_sync": {
                "push_tokens": {
                    token: {
                        "token": token,
                        "platform": "android",
                        "device_name": "phone",
                        "registered_at": "2026-08-02T00:00:00Z",
                    }
                }
            }
        }
    )
    try:
        asyncio.run(namespace["_send_expo_push"](hass, "Title", "Body"))
    finally:
        if original_aiohttp is None:
            sys.modules.pop("aiohttp", None)
        else:
            sys.modules["aiohttp"] = original_aiohttp
        for name in (
            f"{package_name}.const",
            f"{package_name}.automations",
            package_name,
        ):
            sys.modules.pop(name, None)

    assert token not in stream.getvalue()


def test_automation_engine_expo_push_never_logs_skipped_token() -> None:
    """The AutomationEngine's push helper must not log even a token substring.

    Regression for the credential leak fixed alongside `_send_expo_push`:
    the invalid-token branch used to log `token[:30]`, which still discloses
    enough of an Expo token to be a credential.
    """
    automations_path = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "power_sync"
        / "automations"
        / "__init__.py"
    )
    source = automations_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    engine = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "AutomationEngine"
    )
    method = next(
        node
        for node in engine.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_async_send_expo_push"
    )
    extracted = ast.ClassDef(
        name="_ExtractedEngine",
        bases=[],
        keywords=[],
        body=[method],
        decorator_list=[],
    )
    tree = ast.fix_missing_locations(ast.Module(body=[extracted], type_ignores=[]))

    package_name = "powersync_engine_push_logging_testpkg"
    package = types.ModuleType(package_name)
    package.__path__ = []
    automations = types.ModuleType(f"{package_name}.automations")
    automations.__path__ = []
    const = types.ModuleType(f"{package_name}.const")
    const.DOMAIN = "power_sync"
    sys.modules[package_name] = package
    sys.modules[f"{package_name}.automations"] = automations
    sys.modules[f"{package_name}.const"] = const

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def json(self):
            return {"data": [{"status": "ok", "id": "receipt"}]}

    class ClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            return Response()

    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientSession = ClientSession
    original_aiohttp = sys.modules.get("aiohttp")
    sys.modules["aiohttp"] = aiohttp

    stream = io.StringIO()
    logger = logging.getLogger("powersync.engine_push_logging.regression")
    handler = logging.StreamHandler(stream)
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    namespace = {
        "__name__": f"{package_name}.automations.__init__",
        "__package__": f"{package_name}.automations",
        "_LOGGER": logger,
    }
    exec(compile(tree, str(automations_path), "exec"), namespace)
    engine = object.__new__(namespace["_ExtractedEngine"])

    # A valid-looking token that is NOT an Expo token takes the "skipped" branch,
    # which is the one that used to log a 30-character token prefix.
    leaked_token = "sk_live_51NotAnExpoToken_should_never_appear_in_logs"
    engine._hass = types.SimpleNamespace(
        data={
            "power_sync": {
                "push_tokens": {
                    "device-1": {
                        "token": leaked_token,
                        "platform": "ios",
                        "device_name": "phone",
                    }
                }
            }
        }
    )

    try:
        asyncio.run(namespace["_ExtractedEngine"]._async_send_expo_push(engine, "Body"))
    finally:
        if original_aiohttp is None:
            sys.modules.pop("aiohttp", None)
        else:
            sys.modules["aiohttp"] = original_aiohttp
        sys.modules.pop(f"{package_name}.const", None)
        sys.modules.pop(f"{package_name}.automations", None)
        sys.modules.pop(package_name, None)

    logged = stream.getvalue()
    assert leaked_token not in logged
    assert leaked_token[:30] not in logged
    # Diagnosability is preserved even without the credential.
    assert "phone" in logged
    assert "ios" in logged
