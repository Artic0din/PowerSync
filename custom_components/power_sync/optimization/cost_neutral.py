"""Pure accounting model for the Cost Neutral optimiser mode."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostNeutralBudget:
    """Immutable local-day inputs used to cap discretionary export earnings."""

    supply_charge: float
    measured_import_cost: float
    measured_export_earnings: float
    forecast_import_cost: float
    forecast_natural_export_earnings: float

    @property
    def base_projected_cost(self) -> float:
        return (
            self.supply_charge
            + self.measured_import_cost
            - self.measured_export_earnings
            + self.forecast_import_cost
            - self.forecast_natural_export_earnings
        )

    @property
    def battery_export_earnings_cap(self) -> float:
        return max(0.0, self.base_projected_cost)
