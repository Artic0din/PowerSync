"""Charger adapters that normalize provider state into EVLoadpointState."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .loadpoint import EVLoadpointState


@runtime_checkable
class ChargerAdapter(Protocol):
    """Normalize a charger/provider into EVLoadpointState fields."""

    charger_type: str

    def read_state(
        self,
        *,
        loadpoint_id: str,
        raw: dict[str, Any] | None = None,
    ) -> EVLoadpointState:
        """Return normalized loadpoint state from optional raw provider data."""
        ...


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_optional_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class OCPPChargerAdapter:
    """Thin stub adapter for HACS OCPP charger state."""

    charger_type = "ocpp"

    def read_state(
        self,
        *,
        loadpoint_id: str,
        raw: dict[str, Any] | None = None,
    ) -> EVLoadpointState:
        data = raw or {}
        status = str(data.get("status") or data.get("charger_status") or "").lower()
        connected = bool(
            data.get("connected", status in {"preparing", "charging", "suspendedev", "finishing", "occupied"})
        )
        charging = bool(
            data.get("charging", data.get("actual_charging", status == "charging"))
        )
        if "power_kw" in data:
            power_kw = _as_float(data.get("power_kw"))
        elif "power_w" in data:
            power_kw = _as_float(data.get("power_w")) / 1000.0
        else:
            power_kw = 0.0
        return EVLoadpointState(
            loadpoint_id=loadpoint_id,
            vehicle_id=data.get("vehicle_id"),
            charger_type=self.charger_type,
            connected=connected,
            home=data.get("home"),
            actual_charging=charging,
            power_kw=power_kw,
            current_amps=_as_optional_int(data.get("current_amps", data.get("amps"))),
            target_amps=_as_optional_int(data.get("target_amps")),
            soc=_as_optional_float(data.get("soc")),
            extra={"status": status} if status else {},
        )


class TeslaFleetChargerAdapter:
    """Thin stub adapter for Tesla Fleet / Teslemetry vehicle charge state."""

    charger_type = "tesla_fleet"

    def read_state(
        self,
        *,
        loadpoint_id: str,
        raw: dict[str, Any] | None = None,
    ) -> EVLoadpointState:
        data = raw or {}
        charging_state = str(data.get("charging_state") or data.get("state") or "").lower()
        connected = bool(
            data.get(
                "connected",
                charging_state in {"charging", "starting", "stopped", "complete", "connected"},
            )
        )
        charging = bool(
            data.get("charging", data.get("actual_charging", charging_state == "charging"))
        )
        return EVLoadpointState(
            loadpoint_id=loadpoint_id,
            vehicle_id=data.get("vehicle_id") or data.get("vin"),
            charger_type=self.charger_type,
            connected=connected,
            home=data.get("home"),
            actual_charging=charging,
            power_kw=_as_float(data.get("power_kw", data.get("charger_power"))),
            current_amps=_as_optional_int(data.get("current_amps", data.get("charger_actual_current"))),
            target_amps=_as_optional_int(data.get("target_amps", data.get("charge_current_request"))),
            soc=_as_optional_float(data.get("soc", data.get("battery_level"))),
            extra={"charging_state": charging_state} if charging_state else {},
        )


class GenericSwitchChargerAdapter:
    """Thin stub adapter for generic switch/number charger entities."""

    charger_type = "generic_switch"

    def read_state(
        self,
        *,
        loadpoint_id: str,
        raw: dict[str, Any] | None = None,
    ) -> EVLoadpointState:
        data = raw or {}
        switch_on = bool(data.get("switch_on", data.get("is_on", False)))
        connected = bool(data.get("connected", switch_on or data.get("status") == "connected"))
        charging = bool(data.get("charging", data.get("actual_charging", switch_on)))
        return EVLoadpointState(
            loadpoint_id=loadpoint_id,
            vehicle_id=data.get("vehicle_id"),
            charger_type=self.charger_type,
            connected=connected,
            home=data.get("home"),
            actual_charging=charging,
            power_kw=_as_float(data.get("power_kw")),
            current_amps=_as_optional_int(data.get("current_amps", data.get("amps"))),
            target_amps=_as_optional_int(data.get("target_amps")),
            soc=_as_optional_float(data.get("soc")),
            extra={"switch_on": switch_on},
        )


def get_charger_adapter(charger_type: str) -> ChargerAdapter:
    """Return a stub adapter for a known charger type key."""
    key = str(charger_type or "generic_switch").lower()
    adapters: dict[str, ChargerAdapter] = {
        "ocpp": OCPPChargerAdapter(),
        "tesla_fleet": TeslaFleetChargerAdapter(),
        "teslemetry": TeslaFleetChargerAdapter(),
        "fleet_api": TeslaFleetChargerAdapter(),
        "generic_switch": GenericSwitchChargerAdapter(),
        "generic": GenericSwitchChargerAdapter(),
    }
    return adapters.get(key, GenericSwitchChargerAdapter())
