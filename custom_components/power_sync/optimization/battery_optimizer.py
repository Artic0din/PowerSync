"""
Built-in LP Battery Optimizer for PowerSync.

Uses the HiGHS Linear Programming solver directly (via highspy). Falls back to a
greedy heuristic if highspy is unavailable.

Action model:
- CHARGE: Force grid → battery (LP detects grid_import > load)
- EXPORT: Force battery → grid for profit (LP detects grid_export > 0 AND battery_discharge > 0)
- IDLE: Hold battery at current SOC (set backup reserve = current SOC to prevent discharge)
- SELF_CONSUMPTION: Everything else — battery operates naturally (solar charging, home loads)

We only FORCE the battery when it needs to do something it wouldn't do naturally.
Grid charging and grid exporting require force commands. Everything else is natural
self-consumption behavior.
"""
from __future__ import annotations

from .lp_solver import LpSolverMixin, _LpMatrix, _HighsResult, _LpPeriod
from .greedy_solver import GreedySolverMixin
from .schedule_emit import ScheduleEmitMixin


import logging
import math
import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .schedule_reader import ScheduleAction, OptimizationSchedule

_LOGGER = logging.getLogger(__name__)

from .solver_constants import (  # noqa: E402
    ACTION_THRESHOLD_W,
    BELOW_RESERVE_RECOVERY_HOLD_MARGIN_SOC,
    DEFAULT_EFFICIENCY,
    DEFAULT_EXPORT_PRICE,
    DEFAULT_IMPORT_PRICE,
    LP_FAR_PERIOD_MINUTES,
    LP_MID_HORIZON_HOURS,
    LP_MID_PERIOD_MINUTES,
    LP_NEAR_HORIZON_HOURS,
    LP_POWER_SPLIT_THRESHOLD_KW,
    LP_PRICE_SPLIT_THRESHOLD,
    LP_SOLVER_TIME_LIMIT_SECONDS,
    MODE_PROJECTION_MAX_ITERATIONS,
    PRE_WINDOW_REACHABILITY_MARGIN_SOC,
    PRE_WINDOW_REACHABLE_TARGET_MARGIN_SOC,
    PRE_WINDOW_SOLAR_BUFFER_SOC,
    PRE_WINDOW_SOLAR_CREDIT_FACTOR,
)

# Try to import the HiGHS solver; fall back to greedy if unavailable.
try:
    import highspy

    HIGHS_AVAILABLE = True
except ImportError:
    HIGHS_AVAILABLE = False
    highspy = None
    _LOGGER.warning(
        "highspy not available — using greedy fallback optimizer. "
        "Install highspy for optimal LP-based scheduling."
    )







_UNSET = object()


from .results import OptimizerResult  # noqa: E402 — shared DTO


class BatteryOptimizer(LpSolverMixin, GreedySolverMixin, ScheduleEmitMixin):
    """
    LP-based battery optimizer using the HiGHS solver (highspy).

    Solves a cost-minimization (or self-consumption) LP over a forecast horizon
    and maps the result to battery actions.
    """

    def __init__(
        self,
        capacity_wh: float = 13500,
        max_charge_w: float = 5000,
        max_discharge_w: float = 5000,
        max_grid_import_w: float | None = None,
        max_grid_export_w: float | None = None,
        max_battery_export_w: float | None = None,
        efficiency: float = DEFAULT_EFFICIENCY,
        backup_reserve: float = 0.20,
        hardware_reserve: float | None = None,
        grid_charge_soc_cap: float = 1.0,
        interval_minutes: int = 5,
        horizon_hours: int = 48,
        terminal_weight: float = 1.0,
    ):
        self.capacity_wh = capacity_wh
        self.max_charge_w = max_charge_w
        self.max_discharge_w = max_discharge_w
        self.max_grid_import_w = self._normalize_optional_power_w(max_grid_import_w)
        self.max_grid_export_w = self._normalize_optional_export_power_w(max_grid_export_w)
        self.max_battery_export_w = self._normalize_optional_export_power_w(
            max_battery_export_w
        )
        self.efficiency = efficiency
        self.backup_reserve = backup_reserve
        self.hardware_reserve = max(0.0, min(1.0, float(hardware_reserve or 0.0)))
        self.hardware_reserve_known = hardware_reserve is not None
        self.grid_charge_soc_cap = max(
            0.0,
            min(1.0, float(grid_charge_soc_cap if grid_charge_soc_cap is not None else 1.0)),
        )
        self.interval_minutes = interval_minutes
        self.horizon_hours = horizon_hours
        self.terminal_weight = terminal_weight
        # Set by coordinator when a user-triggered force discharge is active so
        # that the below-reserve adjustment fires at INFO instead of WARNING.
        # (SOC below reserve is expected during intentional force discharge.)
        self.suppress_reserve_warning: bool = False
        self._below_reserve_recovery_target: float | None = None
        self.export_reserve_floor: float = 0.0
        self.export_reserve_floor_slots: list[float] | None = None
        self._active_grid_export_limits_w: list[float | None] | None = None
        # Reconciliation runs after ``optimize`` has restored solve-local state.
        # Retain only the most recent normalized slot caps so post-solve schedule
        # spreading cannot escape the network envelope.
        self._last_grid_export_limits_w: list[float | None] | None = None
        self._prevent_simultaneous_grid_flow = False
        self._quota_import_group_ids: list[str | None] | None = None
        self._quota_export_group_ids: list[str | None] | None = None
        self._quota_import_caps_by_group: dict[str, float] = {}
        self._quota_export_caps_by_group: dict[str, float] = {}

        # Pre-window SOC floor: enforce soc[pre_window_slot - 1] >= target.
        # Used by the coordinator to guarantee the battery is filled before
        # high-value export windows (e.g. Flow Power Happy Hour) when
        # profit_max mode is on. The LP rolling horizon otherwise tends to
        # defer grid-charging to the globally cheapest slots, missing the
        # window for today's HH.
        self.pre_window_soc_target: float = 0.0
        self.pre_window_slot: int | None = None
        self.pre_window_solar_credit_factor: float = PRE_WINDOW_SOLAR_CREDIT_FACTOR
        self.pre_window_solar_buffer_soc: float = PRE_WINDOW_SOLAR_BUFFER_SOC

        # Terminal valuation units. The original LP wrote terminal coefficients
        # as `terminal_price * eff * dt / cap`, which is dimensionally wrong:
        # `terminal_price` is $/kWh, so the correct per-kW objective coefficient
        # is `terminal_price * eff * dt` (no `/cap`). The `/cap` was an
        # artefact of treating terminal_price as "$ per SoC unit" while it's
        # actually "$ per kWh of stored energy"; the cap belongs in the SoC
        # bound *constraints* (which already have it correctly), not the
        # objective. Default True now that the unit error is fixed; kept as
        # a flag so tests can compare behavior. Solar-equipped users see no
        # regression because terminal_price is set from solar export prices
        # (typically ~5c) when solar is in horizon, which keeps the
        # discharge penalty well below avoided-import savings.
        self.use_per_kwh_terminal: bool = True

        # Derived
        self.capacity_kwh = capacity_wh / 1000.0
        self.max_charge_kw = max_charge_w / 1000.0
        self.max_discharge_kw = max_discharge_w / 1000.0
        self.max_grid_import_kw = (
            self.max_grid_import_w / 1000.0
            if self.max_grid_import_w is not None
            else None
        )
        self.max_battery_export_kw = (
            self.max_battery_export_w / 1000.0
            if self.max_battery_export_w is not None
            else None
        )
        self.dt_hours = interval_minutes / 60.0  # time step in hours

    def update_config(
        self,
        capacity_wh: float | None = None,
        max_charge_w: float | None = None,
        max_discharge_w: float | None = None,
        max_grid_import_w: float | None | object = _UNSET,
        max_grid_export_w: float | None | object = _UNSET,
        max_battery_export_w: float | None | object = _UNSET,
        efficiency: float | None = None,
        backup_reserve: float | None = None,
        grid_charge_soc_cap: float | None = None,
        horizon_hours: int | None = None,
    ) -> None:
        """Update optimizer configuration."""
        if capacity_wh is not None:
            self.capacity_wh = capacity_wh
            self.capacity_kwh = capacity_wh / 1000.0
        if max_charge_w is not None:
            self.max_charge_w = max_charge_w
            self.max_charge_kw = max_charge_w / 1000.0
        if max_discharge_w is not None:
            self.max_discharge_w = max_discharge_w
            self.max_discharge_kw = max_discharge_w / 1000.0
        if max_grid_import_w is not _UNSET:
            self.max_grid_import_w = self._normalize_optional_power_w(max_grid_import_w)
            self.max_grid_import_kw = (
                self.max_grid_import_w / 1000.0
                if self.max_grid_import_w is not None
                else None
            )
        if max_grid_export_w is not _UNSET:
            self.max_grid_export_w = self._normalize_optional_export_power_w(max_grid_export_w)
        if max_battery_export_w is not _UNSET:
            self.max_battery_export_w = self._normalize_optional_export_power_w(
                max_battery_export_w
            )
            self.max_battery_export_kw = (
                self.max_battery_export_w / 1000.0
                if self.max_battery_export_w is not None
                else None
            )
        if efficiency is not None:
            self.efficiency = efficiency
        if backup_reserve is not None:
            self.backup_reserve = backup_reserve
        if grid_charge_soc_cap is not None:
            self.grid_charge_soc_cap = max(
                0.0,
                min(1.0, float(grid_charge_soc_cap)),
            )
        if horizon_hours is not None:
            try:
                parsed_horizon = int(float(horizon_hours))
            except (TypeError, ValueError):
                parsed_horizon = None
            if parsed_horizon is not None and parsed_horizon > 0:
                self.horizon_hours = parsed_horizon

    def set_quota_bonus_groups(
        self,
        *,
        import_group_ids: list[str | None] | None,
        import_caps_by_group: dict[str, float] | None,
        export_group_ids: list[str | None] | None,
        export_caps_by_group: dict[str, float] | None,
    ) -> None:
        """Set per-tariff-day marginal buckets for the next solve/reconcile."""
        self._quota_import_group_ids = (
            list(import_group_ids) if import_group_ids is not None else None
        )
        self._quota_export_group_ids = (
            list(export_group_ids) if export_group_ids is not None else None
        )
        self._quota_import_caps_by_group = {
            str(key): max(0.0, float(value))
            for key, value in (import_caps_by_group or {}).items()
        }
        self._quota_export_caps_by_group = {
            str(key): max(0.0, float(value))
            for key, value in (export_caps_by_group or {}).items()
        }

    @staticmethod
    def _normalize_optional_power_w(value: float | None | object) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _normalize_optional_export_power_w(value: float | None | object) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    def _charge_limit_kw(
        self,
        load_kw: float,
        solar_kw: float,
        allow_grid_charge: bool,
    ) -> float:
        """Return feasible battery charge power for a slot."""
        charge_limit = self.max_charge_kw
        if not allow_grid_charge:
            charge_limit = min(charge_limit, max(0.0, solar_kw - load_kw))
        elif self.max_grid_import_kw is not None:
            charge_limit = min(
                charge_limit,
                max(0.0, self.max_grid_import_kw - load_kw + solar_kw),
            )
        return max(0.0, charge_limit)

    def update_hardware_reserve(self, hardware_reserve: float) -> None:
        """Update hardware reserve (from manufacturer's app setting)."""
        self.hardware_reserve = max(0.0, min(1.0, float(hardware_reserve or 0.0)))
        self.hardware_reserve_known = True

    def _natural_self_consumption_floor(self, soc_0: float) -> float:
        """SOC floor for displayed natural home-load battery use."""
        optimizer_reserve = max(0.0, min(1.0, self.backup_reserve))
        current_soc = max(0.0, min(1.0, float(soc_0)))
        if not getattr(self, "hardware_reserve_known", False):
            # Do not invent energy when telemetry is already below the
            # conservative fallback floor. Hold at the observed SOC until an
            # economically selected recovery charge can raise it.
            return min(current_soc, optimizer_reserve)
        hardware_reserve = max(0.0, min(1.0, self.hardware_reserve))
        return min(current_soc, hardware_reserve)

    def _configured_export_reserve_floor(self) -> float:
        """Return the transient reserve floor for forced battery export."""
        slot_floors = getattr(self, "export_reserve_floor_slots", None)
        slot_floor = max(slot_floors) if slot_floors else 0.0
        return max(
            0.0,
            min(1.0, float(getattr(self, "export_reserve_floor", 0.0) or 0.0)),
            max(0.0, min(1.0, float(slot_floor or 0.0))),
        )

    def _configured_export_reserve_floor_for_range(self, start: int, end: int) -> float:
        """Return the transient export floor active for a base-slot range."""
        floor = max(
            0.0,
            min(1.0, float(getattr(self, "export_reserve_floor", 0.0) or 0.0)),
        )
        slot_floors = getattr(self, "export_reserve_floor_slots", None)
        if slot_floors:
            active = slot_floors[max(0, start):max(0, end)]
            if active:
                floor = max(floor, max(0.0, min(1.0, max(active))))
        return floor

    def optimize(
        self,
        import_prices: list[float],
        export_prices: list[float],
        solar_forecast: list[float],
        load_forecast: list[float],
        current_soc: float,
        cost_function: str = "cost",
        acquisition_cost_kwh: float = 0.0,
        allow_battery_export: bool | list[bool] = False,
        block_battery_charge: bool | list[bool] = False,
        allow_grid_charge: bool = True,
        grid_charge_allowed: bool | list[bool] | None = None,
        export_bonus_prices: list[float] | None = None,
        export_bonus_cap_kwh: float | None = None,
        import_bonus_prices: list[float] | None = None,
        import_bonus_cap_kwh: float | None = None,
        export_reserve_floor: float | list[float] | None = None,
        schedule_timestamps: list[datetime] | None = None,
        priority_export_slots: bool | list[bool] | None = None,
        priority_export_enabled: bool = False,
        disable_idle: bool = False,
        grid_export_limits_w: list[float | None] | None = None,
        prevent_simultaneous_grid_flow: bool = False,
    ) -> OptimizerResult:
        """
        Run the LP optimization.

        Args:
            import_prices: Import price per kWh for each time step ($/kWh)
            export_prices: Export price per kWh for each time step ($/kWh)
            solar_forecast: Solar generation per time step (kW)
            load_forecast: Home load per time step (kW)
            current_soc: Current battery SOC (0-1)
            cost_function: Optimization objective (only "cost" is supported)
            allow_battery_export: Whether battery-to-grid export is permitted.
                A per-step list restricts export to explicit windows while still
                allowing solar surplus export.
            block_battery_charge: Whether battery charging is blocked for each
                time step. Used for export-only windows where grid charging
                must not occur even when arbitrage appears profitable.
            allow_grid_charge: Whether the optimizer may charge the battery
                from grid import. When false, solar surplus can still charge
                the battery.
            grid_charge_allowed: Optional per-step forced-grid-charge mask.
                False preserves solar surplus charging but blocks grid top-up.
            import_bonus_prices: Optional per-step import credit/top-up values
                for capped free-import settlement windows.
            import_bonus_cap_kwh: Optional kWh cap for import bonuses.
            schedule_timestamps: Optional per-slot timestamps aligned with the
                price forecast.
            priority_export_slots: Optional per-step export-priority mask.
                When omitted and priority_export_enabled is true, uses
                allow_battery_export.
            priority_export_enabled: Mark provider settlement windows where
                modeled export bonuses may make battery export economic.
            disable_idle: Require ordinary non-forced slots to use the battery
                naturally rather than holding SOC for a later opportunity.
            grid_export_limits_w: Optional per-slot site export caps. A numeric
                zero is a valid no-export limit; None falls back to the scalar
                configured cap for that slot.
            prevent_simultaneous_grid_flow: Add a HiGHS grid-direction binary.
                Enabled only for tariffs whose marginal prices can otherwise
                reward import/export passthrough.

        Returns:
            OptimizerResult with schedule and metadata
        """
        start_time = time.monotonic()

        # Align all arrays to the same length
        n_steps = self._align_forecasts(
            import_prices, export_prices, solar_forecast, load_forecast
        )

        if n_steps == 0:
            _LOGGER.warning("No forecast data available, returning empty schedule")
            return self._empty_result()

        # Pad/truncate arrays
        import_prices = self._pad_array(import_prices, n_steps, DEFAULT_IMPORT_PRICE)
        export_prices = self._pad_array(export_prices, n_steps, DEFAULT_EXPORT_PRICE)
        export_bonus_prices = self._pad_array(
            export_bonus_prices, n_steps, 0.0
        )
        import_bonus_prices = self._pad_array(
            import_bonus_prices, n_steps, 0.0
        )
        solar_forecast = self._pad_array(solar_forecast, n_steps, 0.0)
        load_forecast = self._pad_array(load_forecast, n_steps, 0.0)
        allow_battery_export = self._normalize_battery_export_flags(
            allow_battery_export, n_steps
        )
        block_battery_charge = self._normalize_battery_charge_blocks(
            block_battery_charge, n_steps
        )
        grid_charge_allowed = self._normalize_grid_charge_allowed(
            grid_charge_allowed, n_steps
        )
        effective_priority_export_prices = [
            export_prices[idx] + export_bonus_prices[idx]
            for idx in range(n_steps)
        ]
        priority_export_slots = self._normalize_priority_export_slots(
            priority_export_slots,
            allow_battery_export,
            n_steps,
            priority_export_enabled,
            import_prices,
            effective_priority_export_prices,
            grid_charge_allowed,
        )
        # Priority/provider windows affect export permissions and settlement
        # value only. They must not manufacture a home-load bridge floor: that
        # hidden floor forced recovery charging even when direct future imports
        # were cheaper. Explicit caller-supplied export floors remain supported
        # as forced-export boundaries.
        previous_export_floor = self.export_reserve_floor
        previous_export_floor_slots = self.export_reserve_floor_slots
        previous_grid_export_limits = self._active_grid_export_limits_w
        previous_direction_guard = self._prevent_simultaneous_grid_flow
        self._active_grid_export_limits_w = self._normalize_grid_export_limits(
            grid_export_limits_w, n_steps
        )
        self._last_grid_export_limits_w = (
            list(self._active_grid_export_limits_w)
            if self._active_grid_export_limits_w is not None
            else None
        )
        self._prevent_simultaneous_grid_flow = bool(prevent_simultaneous_grid_flow)
        if export_reserve_floor is not None:
            if isinstance(export_reserve_floor, list):
                floors = [
                    max(0.0, min(1.0, float(value or 0.0)))
                    for value in export_reserve_floor[:n_steps]
                ]
                if len(floors) < n_steps:
                    floors.extend([0.0] * (n_steps - len(floors)))
                self.export_reserve_floor = 0.0
                self.export_reserve_floor_slots = floors
            else:
                self.export_reserve_floor = max(
                    0.0,
                    min(1.0, float(export_reserve_floor)),
                )
                self.export_reserve_floor_slots = None

        modeled_backup_reserve = max(0.0, min(1.0, self.backup_reserve))
        modeled_export_reserve_floor = max(
            0.0,
            min(1.0, float(self.export_reserve_floor or 0.0)),
        )
        modeled_export_reserve_floor_slots = (
            list(self.export_reserve_floor_slots)
            if self.export_reserve_floor_slots is not None
            else None
        )

        try:
            if HIGHS_AVAILABLE:
                try:
                    result = self._solve_lp(
                        n_steps,
                        import_prices,
                        export_prices,
                        solar_forecast,
                        load_forecast,
                        current_soc,
                        cost_function,
                        acquisition_cost_kwh,
                        allow_battery_export,
                        block_battery_charge,
                        allow_grid_charge,
                        grid_charge_allowed,
                        export_bonus_prices,
                        export_bonus_cap_kwh,
                        import_bonus_prices,
                        import_bonus_cap_kwh,
                        schedule_timestamps,
                        priority_export_slots,
                        disable_idle,
                    )
                    result.solve_time_s = time.monotonic() - start_time
                    result.modeled_backup_reserve = modeled_backup_reserve
                    result.modeled_export_reserve_floor = modeled_export_reserve_floor
                    result.modeled_export_reserve_floor_slots = (
                        modeled_export_reserve_floor_slots
                    )
                    return result
                except Exception as e:
                    _LOGGER.error(f"LP solver failed, falling back to greedy: {e}")

            # Greedy fallback
            result = self._solve_greedy(
                n_steps,
                import_prices,
                export_prices,
                solar_forecast,
                load_forecast,
                current_soc,
                cost_function,
                acquisition_cost_kwh,
                allow_battery_export,
                block_battery_charge,
                allow_grid_charge,
                grid_charge_allowed,
                export_bonus_prices,
                export_bonus_cap_kwh,
                import_bonus_prices,
                import_bonus_cap_kwh,
                schedule_timestamps,
                priority_export_slots,
                disable_idle,
            )
            result.solve_time_s = time.monotonic() - start_time
            result.modeled_backup_reserve = modeled_backup_reserve
            result.modeled_export_reserve_floor = modeled_export_reserve_floor
            result.modeled_export_reserve_floor_slots = (
                modeled_export_reserve_floor_slots
            )
            return result
        finally:
            if export_reserve_floor is not None:
                self.export_reserve_floor = previous_export_floor
                self.export_reserve_floor_slots = previous_export_floor_slots
            self._active_grid_export_limits_w = previous_grid_export_limits
            self._prevent_simultaneous_grid_flow = previous_direction_guard



    def _normalize_battery_export_flags(
        self,
        allow_battery_export: bool | list[bool],
        target_len: int,
    ) -> list[bool]:
        """Normalize battery export permission into one flag per time step."""
        if isinstance(allow_battery_export, bool):
            return [allow_battery_export] * target_len

        flags = [bool(v) for v in allow_battery_export[:target_len]]
        if len(flags) < target_len:
            flags.extend([False] * (target_len - len(flags)))
        return flags

    def _normalize_battery_charge_blocks(
        self,
        block_battery_charge: bool | list[bool],
        target_len: int,
    ) -> list[bool]:
        """Normalize battery-charge blocking into one flag per time step."""
        if isinstance(block_battery_charge, bool):
            return [block_battery_charge] * target_len

        flags = [bool(v) for v in block_battery_charge[:target_len]]
        if len(flags) < target_len:
            flags.extend([False] * (target_len - len(flags)))
        return flags

    def _normalize_grid_charge_allowed(
        self,
        grid_charge_allowed: bool | list[bool] | None,
        target_len: int,
    ) -> list[bool]:
        """Normalize grid-charge permission into one flag per time step."""
        if grid_charge_allowed is None:
            return [True] * target_len
        if isinstance(grid_charge_allowed, bool):
            return [grid_charge_allowed] * target_len

        flags = [bool(v) for v in grid_charge_allowed[:target_len]]
        if len(flags) < target_len:
            flags.extend([True] * (target_len - len(flags)))
        return flags

    def _normalize_grid_export_limits(
        self,
        values: list[float | None] | None,
        target_len: int,
    ) -> list[float | None] | None:
        if values is None:
            return None
        normalized: list[float | None] = []
        scalar = self.max_grid_export_w
        for value in values[:target_len]:
            try:
                parsed = float(value) if value is not None else None
            except (TypeError, ValueError):
                parsed = None
            if parsed is not None:
                parsed = max(0.0, parsed)
                if scalar is not None:
                    parsed = min(parsed, scalar)
            else:
                parsed = scalar
            normalized.append(parsed)
        while len(normalized) < target_len:
            normalized.append(scalar)
        return normalized

    def _grid_export_limit_kw_for_range(
        self,
        start: int,
        end: int,
        *,
        default_kw: float | None = None,
    ) -> float | None:
        limits = getattr(self, "_active_grid_export_limits_w", None)
        if limits is None:
            limits = getattr(self, "_last_grid_export_limits_w", None)
        active: list[float] = []
        if limits:
            active = [
                max(0.0, float(value)) / 1000.0
                for value in limits[max(0, start):max(0, end)]
                if value is not None
            ]
        if active:
            limit = min(active)
        elif self.max_grid_export_w is not None:
            limit = max(0.0, self.max_grid_export_w / 1000.0)
        else:
            limit = default_kw
        return limit

    def _normalize_priority_export_slots(
        self,
        priority_export_slots: bool | list[bool] | None,
        allow_battery_export: list[bool],
        target_len: int,
        enabled: bool,
        import_prices: list[float],
        export_prices: list[float],
        grid_charge_allowed: list[bool],
    ) -> list[bool]:
        """Return export-priority slots that should prefer surplus export."""
        if not enabled:
            return [False] * target_len

        if priority_export_slots is None:
            raw = list(allow_battery_export[:target_len])
        elif isinstance(priority_export_slots, bool):
            raw = [priority_export_slots] * target_len
        else:
            raw = [bool(v) for v in priority_export_slots[:target_len]]
            if len(raw) < target_len:
                raw.extend([False] * (target_len - len(raw)))

        flags: list[bool] = []
        round_trip_eff = max(0.0, min(1.0, self.efficiency)) ** 2
        for idx in range(target_len):
            if not raw[idx] or not allow_battery_export[idx]:
                flags.append(False)
                continue
            try:
                export_price = float(export_prices[idx] or 0.0)
                import_price = float(import_prices[idx] or 0.0)
            except (TypeError, ValueError):
                flags.append(False)
                continue
            if export_price <= 0.001:
                flags.append(False)
                continue
            # If this slot is already cheap enough to refill the battery after
            # efficiency losses, leave it available for charging. Priority
            # export is for high-price sell windows where the excess can be
            # replenished later, not for cheap import windows.
            if grid_charge_allowed[idx] and import_price <= export_price * round_trip_eff:
                flags.append(False)
                continue
            flags.append(True)
        return flags

    def _priority_export_reserve_floor_slots(
        self,
        import_prices: list[float],
        export_prices: list[float],
        solar: list[float],
        load: list[float],
        priority_export_slots: list[bool],
        block_battery_charge: list[bool],
        allow_grid_charge: bool,
        grid_charge_allowed: list[bool],
        import_bonus_prices: list[float] | None = None,
    ) -> list[float] | None:
        """Build per-slot export floors that bridge home load to next recharge."""
        if not any(priority_export_slots):
            return None
        if self.capacity_kwh <= 0:
            return None

        n = len(priority_export_slots)
        import_bonus_prices = import_bonus_prices or [0.0] * n
        floors = [0.0] * n
        base_floor = max(
            0.0,
            min(1.0, self.backup_reserve),
            min(1.0, self.hardware_reserve),
        )
        round_trip_eff = max(0.0, min(1.0, self.efficiency)) ** 2
        threshold_kw = ACTION_THRESHOLD_W / 1000.0
        idx = 0

        def _forecast_kw(values: list[float], pos: int) -> float:
            if pos >= len(values):
                return 0.0
            try:
                return max(0.0, float(values[pos] or 0.0))
            except (TypeError, ValueError):
                return 0.0

        while idx < n:
            if not priority_export_slots[idx]:
                idx += 1
                continue

            start = idx
            while idx < n and priority_export_slots[idx]:
                idx += 1
            end = idx
            reference_export = max(
                max(0.0, float(price or 0.0))
                for price in export_prices[start:end]
            )
            cheap_recharge_price = reference_export * round_trip_eff
            bridge_kwh = 0.0

            for scan_idx in range(end, n):
                solar_kw = _forecast_kw(solar, scan_idx)
                load_kw = _forecast_kw(load, scan_idx)
                if solar_kw - load_kw > threshold_kw:
                    break
                effective_import_price = import_prices[scan_idx] - (
                    import_bonus_prices[scan_idx]
                    if scan_idx < len(import_bonus_prices)
                    else 0.0
                )
                if (
                    allow_grid_charge
                    and scan_idx < len(grid_charge_allowed)
                    and grid_charge_allowed[scan_idx]
                    and not block_battery_charge[scan_idx]
                    and effective_import_price <= cheap_recharge_price
                    and self._charge_limit_kw(load_kw, solar_kw, True) > threshold_kw
                ):
                    break
                bridge_kwh += max(0.0, load_kw - solar_kw) * self.dt_hours

            bridge_soc = bridge_kwh / max(self.capacity_kwh * self.efficiency, 0.001)
            floor = max(base_floor, min(1.0, base_floor + bridge_soc))
            for floor_idx in range(start, end):
                floors[floor_idx] = floor

        return floors if any(value > 0 for value in floors) else None

    @staticmethod
    def _merge_export_reserve_floor(
        explicit_floor: float | list[float] | None,
        priority_floor: list[float] | None,
        target_len: int,
    ) -> float | list[float] | None:
        """Merge user/auto export floors with priority-export bridge floors."""
        if priority_floor is None:
            return explicit_floor
        if explicit_floor is None:
            return priority_floor

        if isinstance(explicit_floor, list):
            explicit = [
                max(0.0, min(1.0, float(value or 0.0)))
                for value in explicit_floor[:target_len]
            ]
            if len(explicit) < target_len:
                explicit.extend([0.0] * (target_len - len(explicit)))
        else:
            explicit_value = max(0.0, min(1.0, float(explicit_floor or 0.0)))
            explicit = [explicit_value] * target_len
        return [
            max(explicit[idx], priority_floor[idx] if idx < len(priority_floor) else 0.0)
            for idx in range(target_len)
        ]

    def _has_future_self_consumption_value(
        self,
        t: int,
        n: int,
        import_prices: list[float],
        solar: list[float],
        load: list[float],
    ) -> bool:
        """Return True when charging now can avoid later higher-price load."""
        return any(
            import_prices[i] > import_prices[t] + 0.001
            and max(0.0, load[i] - solar[i]) > 0.05
            for i in range(t + 1, n)
        )

    def _future_self_consumption_values(
        self,
        n: int,
        import_prices: list[float],
        solar: list[float],
        load: list[float],
    ) -> list[bool]:
        """Precompute whether each period has later higher-price net load."""
        future_values = [False] * n
        best_future_price = float("-inf")

        for t in range(n - 1, -1, -1):
            future_values[t] = best_future_price > import_prices[t] + 0.001
            if max(0.0, load[t] - solar[t]) > 0.05:
                best_future_price = max(best_future_price, import_prices[t])

        return future_values

    @staticmethod
    def _effective_export_acquisition_costs(
        n: int,
        import_prices: list[float],
        block_battery_charge: list[bool],
        allow_grid_charge: bool,
        acquisition_cost_kwh: float,
        grid_charge_allowed: list[bool] | None = None,
    ) -> list[float]:
        """Return the best known acquisition cost available before each slot."""
        if acquisition_cost_kwh <= 0:
            return [0.0] * n

        costs: list[float] = []
        cheapest_prior_charge: float | None = None
        grid_charge_allowed = grid_charge_allowed or [True] * n
        for t in range(n):
            effective_cost = acquisition_cost_kwh
            if cheapest_prior_charge is not None:
                effective_cost = min(effective_cost, cheapest_prior_charge)
            costs.append(effective_cost)

            if (
                allow_grid_charge
                and not block_battery_charge[t]
                and grid_charge_allowed[t]
            ):
                try:
                    import_price = float(import_prices[t] or 0.0)
                except (TypeError, ValueError):
                    continue
                if cheapest_prior_charge is None:
                    cheapest_prior_charge = import_price
                else:
                    cheapest_prior_charge = min(cheapest_prior_charge, import_price)

        return costs

    @staticmethod
    def _is_export_profitable(
        export_price: float,
        import_price: float,
        acquisition_cost_kwh: float,
        effective_acquisition_cost_kwh: float,
    ) -> bool:
        """Return True when a slot can intentionally export battery energy."""
        if export_price <= 0.001:
            return False

        if export_price > import_price:
            return (
                acquisition_cost_kwh <= 0
                or export_price >= effective_acquisition_cost_kwh
            )

        # Some tariffs fill the battery cheaply before a lower-FIT export
        # window. The current import price is still relevant to self-consumption,
        # but it should not completely block exporting energy that was acquired
        # below the export rate.
        return (
            acquisition_cost_kwh > 0
            and export_price >= effective_acquisition_cost_kwh
        )



    def _period_index_for_base_slot(
        self,
        periods: list[_LpPeriod],
        base_slot: int,
    ) -> int:
        """Return the internal boundary index matching a base slot deadline."""
        for idx, period in enumerate(periods):
            if period.end >= base_slot:
                return idx + 1 if period.end == base_slot else idx
        return len(periods)

    def _pre_window_solar_prefill_ceilings(
        self,
        *,
        pre_window_boundary: int | None,
        target_soc: float | None,
        solar: list[float],
        load: list[float],
        dt_hours: list[float],
        reserve_floor: list[float],
        current_soc: float,
        charge_pinned: list[bool] | None = None,
    ) -> list[float | None]:
        """Return SOC upper bounds that leave room for forecast solar."""
        p_n = len(solar)
        ceilings: list[float | None] = [None] * (p_n + 1)
        if (
            pre_window_boundary is None
            or target_soc is None
            or pre_window_boundary <= 1
            or pre_window_boundary > p_n
            or self.capacity_kwh <= 0
            or self.max_charge_kw <= 0
        ):
            return ceilings

        credit_factor = max(0.0, min(1.0, self.pre_window_solar_credit_factor))
        if credit_factor <= 0:
            return ceilings

        buffer_soc = max(0.0, self.pre_window_solar_buffer_soc)
        remaining_solar_kwh = [0.0] * (p_n + 1)
        for idx in range(pre_window_boundary - 1, -1, -1):
            # Solar surplus can only be stored in periods where charging is
            # actually permitted. Crediting surplus in charge-blocked or
            # export-suppressed periods holds the pre-window SOC ceiling too
            # low to ever meet the deadline floor, making the LP infeasible.
            if charge_pinned is not None and charge_pinned[idx]:
                remaining_solar_kwh[idx] = remaining_solar_kwh[idx + 1]
                continue
            surplus_kw = max(0.0, solar[idx] - load[idx])
            usable_kw = min(self.max_charge_kw, surplus_kw)
            stored_kwh = usable_kw * self.efficiency * dt_hours[idx] * credit_factor
            remaining_solar_kwh[idx] = remaining_solar_kwh[idx + 1] + stored_kwh

        active_count = 0
        min_ceiling = 1.0
        for boundary in range(1, pre_window_boundary):
            remaining_soc = remaining_solar_kwh[boundary] / self.capacity_kwh
            if remaining_soc <= 1e-6:
                continue

            ceiling = target_soc - remaining_soc + buffer_soc
            # Never force a discharge just to make room. This only limits
            # additional prefill above the current SOC.
            ceiling = max(
                current_soc,
                reserve_floor[boundary],
                min(1.0, ceiling),
            )
            ceiling = max(0.0, min(1.0, ceiling))
            if ceiling < 1.0 - 1e-6:
                ceilings[boundary] = ceiling
                active_count += 1
                min_ceiling = min(min_ceiling, ceiling)

        if active_count:
            _LOGGER.debug(
                "Solar-aware pre-window ceiling: %d boundaries, min %.1f%% "
                "(target %.1f%%, credit %.0f%%, buffer %.1f%%)",
                active_count,
                min_ceiling * 100,
                target_soc * 100,
                credit_factor * 100,
                buffer_soc * 100,
            )

        return ceilings

    def _expand_period_values(
        self,
        periods: list[_LpPeriod],
        values: list[float],
        n: int,
    ) -> list[float]:
        """Expand internal period values back to base schedule slots."""
        expanded = [0.0] * n
        for period, value in zip(periods, values):
            for base_idx in range(period.start, period.end):
                expanded[base_idx] = value
        return expanded

    @staticmethod
    def _schedule_mode_constraints(
        schedule: OptimizationSchedule,
        n: int,
    ) -> tuple[list[str], list[float]]:
        """Return command modes and required natural discharge from a schedule."""
        modes = ["idle"] * n
        required_self_use_kw = [0.0] * n
        for idx, action in enumerate((schedule.actions or [])[:n]):
            if action.action == "charge":
                modes[idx] = "charge"
            elif action.action in ("export", "discharge"):
                modes[idx] = "export"
            elif action.action == "idle":
                modes[idx] = "idle"
            else:
                modes[idx] = "self_use"
                required_self_use_kw[idx] = max(
                    0.0,
                    float(action.battery_discharge_w or 0.0) / 1000.0,
                )
        return modes, required_self_use_kw

    @staticmethod
    def _mode_constraints_match(
        left_modes: list[str],
        left_required: list[float],
        right_modes: list[str],
        right_required: list[float],
    ) -> bool:
        """Return True when two physical command projections are equivalent."""
        if left_modes != right_modes:
            return False

        # Coarse LP periods can shift the exact base slot in which a continuous
        # self-use run reaches its floor. Compare the run's total requested
        # natural discharge rather than requiring an identical sub-slot shape;
        # unlike a modes-only comparison, this still proves the next solve was
        # constrained by the same amount of battery energy that will be emitted.
        idx = 0
        while idx < len(left_modes):
            if left_modes[idx] != "self_use":
                idx += 1
                continue
            end = idx + 1
            while end < len(left_modes) and left_modes[end] == "self_use":
                end += 1
            left_total = sum(left_required[idx:end])
            right_total = sum(right_required[idx:end])
            if not math.isclose(
                left_total,
                right_total,
                rel_tol=1e-4,
                abs_tol=0.01,
            ):
                return False
            idx = end
        return True


    def _export_allowed_after_reserve_recovery(
        self,
        allow_battery_export: list[bool],
        block_battery_charge: list[bool],
        import_prices: list[float],
        export_prices: list[float],
        solar: list[float],
        load: list[float],
        soc_0: float,
        export_floor: float,
        allow_grid_charge: bool,
        grid_charge_allowed: list[bool],
        acquisition_cost_kwh: float,
        export_bonus_prices: list[float],
        priority_export_slots: list[bool],
    ) -> list[bool]:
        """Allow export slots only after charge headroom can recover SOC."""
        if soc_0 >= export_floor:
            return allow_battery_export

        round_trip_eff = max(0.0, min(1.0, self.efficiency)) ** 2
        future_recovery_prices = [0.0] * len(allow_battery_export)
        best_future_export = 0.0
        for idx in range(len(allow_battery_export) - 1, -1, -1):
            effective_export_price = (
                export_prices[idx]
                + (export_bonus_prices[idx] if idx < len(export_bonus_prices) else 0.0)
                if idx < len(export_prices)
                else 0.0
            )
            export_profitable = (
                bool(allow_battery_export[idx])
                and idx < len(import_prices)
                and self._is_export_profitable(
                    effective_export_price,
                    import_prices[idx],
                    acquisition_cost_kwh,
                    acquisition_cost_kwh,
                )
            )
            priority_export = (
                bool(allow_battery_export[idx])
                and idx < len(priority_export_slots)
                and priority_export_slots[idx]
                and effective_export_price > 0.001
            )
            if export_profitable or priority_export:
                best_future_export = max(best_future_export, effective_export_price)
            future_recovery_prices[idx] = best_future_export

        reachable_soc = max(0.0, min(1.0, soc_0))
        recovered: list[bool] = []
        for idx, allowed in enumerate(allow_battery_export):
            effective_export_price = (
                export_prices[idx]
                + (export_bonus_prices[idx] if idx < len(export_bonus_prices) else 0.0)
                if idx < len(export_prices)
                else 0.0
            )
            export_profitable = (
                bool(allowed)
                and idx < len(import_prices)
                and self._is_export_profitable(
                    effective_export_price,
                    import_prices[idx],
                    acquisition_cost_kwh,
                    acquisition_cost_kwh,
                )
            )
            priority_export = (
                bool(allowed)
                and idx < len(priority_export_slots)
                and priority_export_slots[idx]
                and effective_export_price > 0.001
            )
            # Use this slot's own floor, not the horizon-wide maximum: a high
            # floor scoped to a later window (e.g. tomorrow's export bridge)
            # must not block re-enabling export in an earlier window whose
            # real floor is just the optimiser reserve.
            slot_export_floor = max(
                self.backup_reserve,
                self._configured_export_reserve_floor_for_range(idx, idx + 1),
            )
            # Only re-enable export once charge headroom can restore SOC to
            # this slot's floor AND exporting here is actually profitable.
            # Re-allowing unprofitable slots serves no purpose except to raise
            # the LP reserve floor, which force-charges the battery at the
            # current (often peak) price purely to recover the optimiser
            # reserve — the behaviour this below-reserve path exists to avoid.
            recovered.append(
                (export_profitable or priority_export)
                and reachable_soc >= slot_export_floor - 1e-6
            )
            if idx >= len(solar) or idx >= len(load):
                continue
            blocked = idx < len(block_battery_charge) and block_battery_charge[idx]
            if not blocked:
                # During a profitable-export slot the battery exports rather
                # than charges, so it adds no recovery headroom.
                blocked = export_profitable or priority_export
            if blocked:
                continue
            solar_only_charge_kw = self._charge_limit_kw(load[idx], solar[idx], False)
            charge_kw = solar_only_charge_kw
            if (
                allow_grid_charge
                and idx < len(grid_charge_allowed)
                and grid_charge_allowed[idx]
            ):
                future_export_value = (
                    future_recovery_prices[idx + 1]
                    if idx + 1 < len(future_recovery_prices)
                    else 0.0
                )
                try:
                    import_price = float(import_prices[idx])
                except (IndexError, TypeError, ValueError):
                    import_price = None
                if (
                    import_price is not None
                    and future_export_value > 0.001
                    and import_price <= future_export_value * round_trip_eff + 1e-9
                ):
                    charge_kw = self._charge_limit_kw(load[idx], solar[idx], True)
            if charge_kw <= 0:
                continue
            reachable_soc = min(
                1.0,
                reachable_soc
                + charge_kw * self.efficiency * self.dt_hours / self.capacity_kwh,
            )
        return recovered



    def _build_home_load_export_bridge(
        self,
        actions: list[ScheduleAction],
        solar: list[float],
        load: list[float],
    ) -> dict[str, Any]:
        """Return an export-only floor that leaves energy for post-export home load."""
        threshold_w = ACTION_THRESHOLD_W
        best_bridge: dict[str, Any] = {}
        best_floor = 0.0
        idx = 0

        while idx < len(actions):
            if actions[idx].action != "export":
                idx += 1
                continue

            export_start_idx = idx
            while idx < len(actions) and actions[idx].action == "export":
                idx += 1
            bridge_start_idx = idx
            if bridge_start_idx >= len(actions):
                continue

            next_charge_idx: int | None = None
            next_charge_reason: str | None = None
            for scan_idx in range(bridge_start_idx, len(actions)):
                action = actions[scan_idx]
                if action.battery_charge_w > threshold_w:
                    next_charge_idx = scan_idx
                    next_charge_reason = (
                        "scheduled_grid_charge"
                        if action.action == "charge"
                        else "forecast_solar_surplus"
                    )
                    break

                if scan_idx < len(solar) and scan_idx < len(load):
                    if (solar[scan_idx] - load[scan_idx]) * 1000 > threshold_w:
                        next_charge_idx = scan_idx
                        next_charge_reason = "forecast_solar_surplus"
                        break

            bridge_end_exclusive = (
                next_charge_idx
                if next_charge_idx is not None
                else len(actions)
            )
            bridge_kwh = 0.0
            for load_idx in range(bridge_start_idx, bridge_end_exclusive):
                if load_idx >= len(solar) or load_idx >= len(load):
                    break
                bridge_kwh += max(0.0, load[load_idx] - solar[load_idx]) * self.dt_hours

            if bridge_kwh <= 0:
                continue

            bridge_soc = bridge_kwh / max(self.capacity_kwh * self.efficiency, 0.001)
            export_floor = max(
                self.hardware_reserve,
                min(1.0, self.hardware_reserve + bridge_soc),
            )
            if export_floor <= best_floor:
                continue

            best_floor = export_floor
            protects_until_idx = (
                next_charge_idx
                if next_charge_idx is not None
                else len(actions) - 1
            )
            best_bridge = {
                "home_load_export_floor_percent": max(
                    0,
                    min(100, int(round(export_floor * 100))),
                ),
                "home_load_bridge_kwh": round(bridge_kwh, 3),
                "home_load_bridge_start": actions[bridge_start_idx].timestamp.isoformat(),
                "home_load_bridge_until": actions[protects_until_idx].timestamp.isoformat(),
                "home_load_bridge_next_charge_reason": (
                    next_charge_reason or "no_charge_in_horizon"
                ),
                "home_load_bridge_after_export_start": actions[
                    export_start_idx
                ].timestamp.isoformat(),
            }

        return best_bridge




    def _grid_flows_from_schedule(
        self,
        schedule: OptimizationSchedule,
        n: int,
        solar: list[float],
        load: list[float],
    ) -> tuple[list[float], list[float]]:
        """Recompute per-slot grid import/export (kW) from the emitted schedule.

        Applies the power balance grid_import - grid_export = load - solar +
        charge - discharge to the schedule's reported battery charge/discharge,
        so the reported flows describe the displayed actions rather than the raw
        LP solution (which models "hold" slots as grid import while the schedule
        serves that load from the battery).
        """
        grid_import = [0.0] * n
        grid_export = [0.0] * n
        actions = schedule.actions or []
        for t in range(n):
            if t >= len(actions):
                break
            action = actions[t]
            if action.action == "off_grid":
                # OFF_GRID represents an islanded/curtailed site. Any forecast
                # imbalance is absorbed by local control or curtailment, not by
                # a billable grid flow.
                continue
            charge_kw = action.battery_charge_w / 1000.0
            discharge_kw = action.battery_discharge_w / 1000.0
            solar_kw = solar[t] if t < len(solar) else 0.0
            load_kw = load[t] if t < len(load) else 0.0
            net_grid = (load_kw - solar_kw) + charge_kw - discharge_kw
            if net_grid > 0:
                grid_import[t] = net_grid
            else:
                export_kw = -net_grid
                max_grid_export_kw = self._grid_export_limit_kw_for_range(t, t + 1)
                if max_grid_export_kw is not None:
                    export_kw = min(export_kw, max_grid_export_kw)
                grid_export[t] = export_kw
        return grid_import, grid_export



    def _calculate_baseline_cost(
        self,
        n: int,
        import_prices: list[float],
        export_prices: list[float],
        solar: list[float],
        load: list[float],
        *,
        export_bonus_prices: list[float] | None = None,
        export_bonus_cap_kwh: float | None = None,
        import_bonus_prices: list[float] | None = None,
        import_bonus_cap_kwh: float | None = None,
    ) -> float:
        """
        Calculate baseline cost without battery.

        All load from grid, all excess solar exported.
        """
        dt = self.dt_hours
        bonus_prices = export_bonus_prices or [0.0] * n
        import_bonus = import_bonus_prices or [0.0] * n
        baseline_import = [max(0.0, load[t] - solar[t]) for t in range(n)]
        baseline_export = [max(0.0, solar[t] - load[t]) for t in range(n)]
        bonus_import_flow = self._allocate_capped_bonus(
            baseline_import,
            import_bonus,
            import_bonus_cap_kwh,
            self._quota_import_group_ids,
            self._quota_import_caps_by_group,
        )
        bonus_export_flow = self._allocate_capped_bonus(
            baseline_export,
            bonus_prices,
            export_bonus_cap_kwh,
            self._quota_export_group_ids,
            self._quota_export_caps_by_group,
        )

        cost = sum(
            import_prices[t] * baseline_import[t] * dt
            - import_bonus[t] * bonus_import_flow[t] * dt
            - export_prices[t] * baseline_export[t] * dt
            - bonus_prices[t] * bonus_export_flow[t] * dt
            for t in range(n)
        )

        return round(cost, 2)

    def _empty_result(self) -> OptimizerResult:
        """Return an empty result when no data is available."""
        return OptimizerResult(
            schedule=OptimizationSchedule(
                actions=[],
                predicted_cost=0.0,
                predicted_savings=0.0,
                last_updated=dt_util.now(),
            ),
            solver_used="none",
            feasible=False,
        )
