"""Shared optimizer result DTO (avoids circular imports with solver mixins)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schedule_reader import OptimizationSchedule


@dataclass
class OptimizerResult:
    """Result from the LP optimizer."""

    schedule: OptimizationSchedule
    solve_time_s: float = 0.0
    objective_value: float = 0.0
    solver_used: str = "greedy"
    feasible: bool = True
    grid_import_w: list[float] = field(default_factory=list)
    grid_export_w: list[float] = field(default_factory=list)
    lp_stats: dict[str, Any] = field(default_factory=dict)
    reserve_recommendation: dict[str, Any] = field(default_factory=dict)
    modeled_backup_reserve: float | None = None
    modeled_export_reserve_floor: float | None = None
    modeled_export_reserve_floor_slots: list[float] | None = None
