"""Curtailment policy package.

Re-exports the existing inverter factory; policy orchestration moves here
from ``async_setup_entry`` in later strangler steps.
"""

from ..inverters import get_inverter_controller

__all__ = ["get_inverter_controller"]
