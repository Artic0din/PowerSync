"""Regression tests for descriptive optimizer AI summaries."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = (
    ROOT / "custom_components" / "power_sync" / "optimization" / "ai_summary.py"
)
INIT_PATH = ROOT / "custom_components" / "power_sync" / "__init__.py"


def _load_module():
    names = (
        "custom_components",
        "custom_components.power_sync",
        "custom_components.power_sync.optimization",
    )
    previous = {name: sys.modules.get(name) for name in names}
    previous_const = sys.modules.get("custom_components.power_sync.const")
    try:
        for name in names:
            package = ModuleType(name)
            package.__path__ = []
            sys.modules[name] = package
        const = ModuleType("custom_components.power_sync.const")
        const.CONF_OPTIMIZATION_AI_SUMMARY_API_KEY = "optimization_ai_summary_api_key"
        const.CONF_OPTIMIZATION_AI_SUMMARY_PROVIDER = "optimization_ai_summary_provider"
        const.DEFAULT_OPTIMIZATION_AI_SUMMARY_PROVIDER = "gemini"
        sys.modules[const.__name__] = const

        spec = importlib.util.spec_from_file_location(
            "custom_components.power_sync.optimization.ai_summary",
            MODULE_PATH,
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if previous_const is None:
            sys.modules.pop("custom_components.power_sync.const", None)
        else:
            sys.modules["custom_components.power_sync.const"] = previous_const


def _snapshot() -> dict:
    return {
        "optimizer_available": True,
        "optimization_status": "active",
        "last_optimization": "2026-08-01T12:00:00+10:00",
        "monitoring_mode": False,
        "planned_current_action": "charge",
        "current_action": "charge",
        "current_action_end_time": "2026-08-01T12:30:00+10:00",
        "next_action": "export",
        "next_action_time": "2026-08-01T13:00:00+10:00",
        "predicted_cost": 1.2,
        "predicted_savings": 2.3,
        "schedule": {
            "timestamps": [
                "2026-08-01T12:00:00+10:00",
                "2026-08-01T12:30:00+10:00",
                "2026-08-01T13:00:00+10:00",
                "2026-08-01T13:30:00+10:00",
            ],
            "import_price": [10.0, 12.0, 40.0, 45.0],
            "export_price": [3.0, 4.0, 65.0, 70.0],
            "soc": [0.25, 0.35, 0.45, 0.38],
            "serial_number": "must-not-leak",
        },
        "next_actions": [
            {
                "timestamp": "2026-08-01T12:00:00+10:00",
                "end_time": "2026-08-01T13:00:00+10:00",
                "action": "charge",
                "power_w": 5000,
                "soc": 0.25,
            },
            {
                "timestamp": "2026-08-01T13:00:00+10:00",
                "end_time": "2026-08-01T14:00:00+10:00",
                "action": "export",
                "power_w": 4500,
                "soc": 0.45,
            },
        ],
        "summary": {
            "total_cost": 1.2,
            "total_import_kwh": 5.0,
            "total_export_kwh": 3.5,
            "savings": 2.3,
        },
        "forecast_summary": {
            "load_today_remaining_kwh": 8.4,
            "load_tomorrow_kwh": 18.1,
            "load_peak_kw": 4.2,
            "history_diagnostics": {"entity_id": "sensor.private"},
        },
        "warnings": [
            {
                "type": "forecast",
                "title": "Forecast limited",
                "message": "Solar forecast is incomplete.",
                "api_key": "must-not-leak",
            }
        ],
        "config": {
            "backup_reserve": 0.2,
            "allow_grid_charge": True,
            "max_grid_export_w": 5000,
            "interval_minutes": 30,
            "horizon_hours": 48,
            "planned_ev_load_entity": "sensor.private_ev",
        },
        "ev": {
            "deadline": "2026-08-02T06:00:00+10:00",
            "required_energy_kwh": 14,
            "vin": "must-not-leak",
            "vehicles": [{"name": "private"}],
        },
        "planned_ev_load_kwh": 10.5,
        "planned_ev_load_peak_kw": 7.2,
        "api_key": "must-not-leak",
        "entity_id": "sensor.private",
    }


def _model_output() -> dict:
    return {
        "overview": "The plan charges before a valuable export period.",
        "strategy": "It shifts stored energy into the strongest supplied price window.",
        "action_explanations": [
            {"window_id": "w1", "reason": "This window has the highest supplied export price."}
        ],
        "caveats": ["Forecast inputs may change."],
    }


def test_context_is_compact_deterministic_and_excludes_private_fields():
    module = _load_module()
    context = module.build_compact_context(_snapshot())
    serialized = module.canonical_context_json(context)

    assert [window["window_id"] for window in context["action_windows"]] == ["w0", "w1"]
    assert context["action_windows"][0]["import_price"] == {
        "min": 10.0,
        "average": 11.0,
        "max": 12.0,
    }
    assert context["action_windows"][1]["export_price"]["average"] == 67.5
    assert context["horizon"] == {
        "start": "2026-08-01T12:00:00+10:00",
        "end": "2026-08-01T14:00:00+10:00",
        "hours": 24,
    }
    assert context["summary"] == {
        "predicted_cost_today": 1.2,
        "predicted_savings_today": 2.3,
    }
    for secret in (
        "must-not-leak",
        "sensor.private",
        "sensor.private_ev",
        "serial_number",
        '"vin"',
        '"api_key"',
        '"vehicles"',
        "history_diagnostics",
    ):
        assert secret not in serialized

    reordered = dict(reversed(list(context.items())))
    assert module.context_fingerprint(context, "gemini", "model") == module.context_fingerprint(
        reordered, "gemini", "model"
    )
    live_status_changed = {**context, "current_action": "export", "plan_generated_at": "later"}
    assert module.context_fingerprint(context, "gemini", "model") == module.context_fingerprint(
        live_status_changed, "gemini", "model"
    )


def test_context_distinguishes_soft_missing_inputs_from_unusable_plan():
    module = _load_module()
    snapshot = _snapshot()
    snapshot["schedule"].pop("import_price")
    snapshot["schedule"].pop("export_price")
    snapshot.pop("forecast_summary")
    snapshot.pop("predicted_cost")
    snapshot.pop("predicted_savings")
    snapshot.pop("ev")
    snapshot.pop("planned_ev_load_kwh")
    snapshot.pop("planned_ev_load_peak_kw")

    context = module.build_compact_context(snapshot)
    assert context["missing_inputs"] == [
        "cost_and_energy_summary",
        "ev_plan",
        "export_prices",
        "forecast_summary",
        "import_prices",
    ]

    snapshot["next_actions"] = []
    with pytest.raises(module.AISummaryError, match="usable action plan") as caught:
        module.build_compact_context(snapshot)
    assert caught.value.code == "optimizer_unavailable"


def test_model_output_is_strict_and_server_owns_action_metadata():
    module = _load_module()
    context = module.build_compact_context(_snapshot())
    output = module.validate_model_output(_model_output(), context["action_windows"])

    assert output["important_actions"] == [
        {
            "window_id": "w1",
            "start": "2026-08-01T13:00:00+10:00",
            "end": "2026-08-01T14:00:00+10:00",
            "action": "export",
            "reason": "This window has the highest supplied export price.",
        }
    ]

    unknown = _model_output()
    unknown["action_explanations"] = [{"window_id": "w99", "reason": "invented"}]
    with pytest.raises(module.AISummaryError) as caught:
        module.validate_model_output(unknown, context["action_windows"])
    assert caught.value.code == "invalid_provider_response"

    extra = {**_model_output(), "hardware_command": "force_charge"}
    with pytest.raises(module.AISummaryError):
        module.validate_model_output(extra, context["action_windows"])

    excessive = _model_output()
    excessive["overview"] = "x" * 501
    with pytest.raises(module.AISummaryError):
        module.validate_model_output(excessive, context["action_windows"])


def test_write_only_settings_replace_preserve_clear_and_require_provider_key():
    module = _load_module()
    key_name = module.CONF_OPTIMIZATION_AI_SUMMARY_API_KEY
    provider_name = module.CONF_OPTIMIZATION_AI_SUMMARY_PROVIDER

    options, changes = module.apply_ai_summary_settings(
        {}, {"ai_summary_provider": "gemini", "ai_summary_api_key": "secret-key"}
    )
    assert options == {provider_name: "gemini", key_name: "secret-key"}
    assert changes == ["updated AI summary API key"]

    preserved, changes = module.apply_ai_summary_settings(
        options, {"ai_summary_api_key": "   "}
    )
    assert preserved[key_name] == "secret-key"
    assert changes == []

    with pytest.raises(module.AISummaryError) as caught:
        module.apply_ai_summary_settings(options, {"ai_summary_provider": "grok"})
    assert caught.value.code == "ai_provider_key_required"
    assert options[provider_name] == "gemini"

    changed, _ = module.apply_ai_summary_settings(
        options,
        {"ai_summary_provider": "grok", "ai_summary_api_key": "grok-secret"},
    )
    assert changed == {provider_name: "grok", key_name: "grok-secret"}

    cleared, changes = module.apply_ai_summary_settings(
        changed, {"clear_ai_summary_api_key": True}
    )
    assert key_name not in cleared
    assert changes == ["cleared AI summary API key"]

    public = module.ai_summary_settings(SimpleNamespace(data={}, options=changed))
    assert public == {
        "ai_summary_provider": "grok",
        "ai_summary_key_configured": True,
        "ai_summary_model": "grok-4.5",
    }
    assert "grok-secret" not in json.dumps(public)


class _FakeResponse:
    def __init__(self, status: int, body):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class _TimeoutContext:
    async def __aenter__(self):
        raise asyncio.TimeoutError

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _TimeoutSession:
    def post(self, url, **kwargs):
        return _TimeoutContext()


def test_provider_adapters_use_structured_requests_without_key_in_body():
    module = _load_module()
    context = module.build_compact_context(_snapshot())

    gemini_session = _FakeSession(
        _FakeResponse(
            200,
            {
                "steps": [
                    {
                        "type": "model_output",
                        "content": [
                            {"type": "text", "text": json.dumps(_model_output())}
                        ],
                    }
                ]
            },
        )
    )
    result = asyncio.run(
        module.GeminiAISummaryProvider().generate(
            session=gemini_session,
            api_key="gemini-secret",
            model="gemini-3.5-flash-lite",
            context=context,
        )
    )
    assert result == _model_output()
    gemini_url, gemini_request = gemini_session.calls[0]
    assert gemini_url.endswith("/v1beta/interactions")
    assert gemini_request["headers"]["x-goog-api-key"] == "gemini-secret"
    assert gemini_request["json"]["store"] is False
    assert "temperature" not in gemini_request["json"]["generation_config"]
    assert gemini_request["json"]["response_format"]["schema"] == module.MODEL_OUTPUT_SCHEMA
    assert "gemini-secret" not in json.dumps(gemini_request["json"])

    grok_session = _FakeSession(
        _FakeResponse(
            200,
            {"choices": [{"message": {"content": json.dumps(_model_output())}}]},
        )
    )
    result = asyncio.run(
        module.GrokAISummaryProvider().generate(
            session=grok_session,
            api_key="grok-secret",
            model="grok-4.5",
            context=context,
        )
    )
    assert result == _model_output()
    grok_url, grok_request = grok_session.calls[0]
    assert grok_url.endswith("/v1/chat/completions")
    assert grok_request["headers"]["Authorization"] == "Bearer grok-secret"
    assert grok_request["json"]["response_format"]["json_schema"]["strict"] is True
    assert "grok-secret" not in json.dumps(grok_request["json"])


@pytest.mark.parametrize(
    ("status", "code", "http_status"),
    (
        (401, "provider_auth_failed", 502),
        (403, "provider_auth_failed", 502),
        (429, "provider_rate_limited", 429),
        (500, "provider_unavailable", 502),
        (400, "provider_rejected", 502),
    ),
)
def test_provider_http_errors_are_safe_and_not_ha_auth(status, code, http_status):
    module = _load_module()
    session = _FakeSession(_FakeResponse(status, {"error": "secret upstream body"}))
    with pytest.raises(module.AISummaryError) as caught:
        asyncio.run(
            module.GrokAISummaryProvider().generate(
                session=session,
                api_key="not-returned",
                model="grok-4.5",
                context=module.build_compact_context(_snapshot()),
            )
        )
    assert caught.value.code == code
    assert caught.value.http_status == http_status
    assert "secret upstream body" not in caught.value.message
    assert "not-returned" not in caught.value.message


def test_provider_timeout_and_malformed_json_have_stable_errors():
    module = _load_module()
    context = module.build_compact_context(_snapshot())
    with pytest.raises(module.AISummaryError) as timed_out:
        asyncio.run(
            module.GeminiAISummaryProvider().generate(
                session=_TimeoutSession(),
                api_key="not-returned",
                model="gemini-3.5-flash-lite",
                context=context,
            )
        )
    assert timed_out.value.code == "provider_timeout"
    assert timed_out.value.http_status == 504

    malformed = _FakeSession(_FakeResponse(200, ValueError("bad json")))
    with pytest.raises(module.AISummaryError) as invalid:
        asyncio.run(
            module.GrokAISummaryProvider().generate(
                session=malformed,
                api_key="not-returned",
                model="grok-4.5",
                context=context,
            )
        )
    assert invalid.value.code == "invalid_provider_response"


def test_service_cache_refresh_deduplication_and_plan_change_guard():
    module = _load_module()
    snapshot = _snapshot()

    class Adapter:
        def __init__(self):
            self.calls = 0

        async def generate(self, **kwargs):
            self.calls += 1
            await asyncio.sleep(0)
            return _model_output()

    adapter = Adapter()
    module.PROVIDER_ADAPTERS["gemini"] = adapter
    service = module.AISummaryService(object(), lambda: snapshot)

    async def exercise():
        first, joined = await asyncio.gather(
            service.generate(provider="gemini", api_key="key", refresh=False),
            service.generate(provider="gemini", api_key="key", refresh=False),
        )
        cached = await service.generate(provider="gemini", api_key="key", refresh=False)
        refreshed = await service.generate(provider="gemini", api_key="key", refresh=True)
        return first, joined, cached, refreshed

    first, joined, cached, refreshed = asyncio.run(exercise())
    assert adapter.calls == 2
    assert first["cache_hit"] is False
    assert joined["summary"] == first["summary"]
    assert cached["cache_hit"] is True
    assert refreshed["cache_hit"] is False
    assert service.status(
        snapshot=snapshot, provider="gemini", api_key="key"
    )["state"] == "ready"

    class MutatingAdapter:
        async def generate(self, **kwargs):
            snapshot["next_actions"][1]["action"] = "idle"
            return _model_output()

    service.invalidate()
    module.PROVIDER_ADAPTERS["gemini"] = MutatingAdapter()
    with pytest.raises(module.AISummaryError) as caught:
        asyncio.run(service.generate(provider="gemini", api_key="key", refresh=False))
    assert caught.value.code == "plan_changed"


def test_http_views_register_explicit_generation_and_never_return_key():
    source = INIT_PATH.read_text()
    assert "hass.http.register_view(OptimizationAISummaryView(hass))" in source
    assert 'url = "/api/power_sync/optimization/ai_summary"' in source
    optimization_get = source[
        source.index("class OptimizationView") : source.index("class OptimizationSettingsView")
    ]
    assert ".generate(" not in optimization_get
    summary_view = source[source.index("class OptimizationAISummaryView") :]
    assert 'set(payload) - {"refresh"}' in summary_view
    assert 'api_key=configured_api_key(config_entry)' in summary_view
    assert '"ai_summary_api_key"' not in summary_view
    filter_source = source[
        source.index("class SensitiveDataFilter") : source.index("class CalendarHistoryView")
    ]
    assert "xai-[a-zA-Z0-9_-]" in filter_source
    assert "AIza[a-zA-Z0-9_-]" in filter_source
