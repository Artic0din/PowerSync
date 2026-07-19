"""Tariff sync package (TOU upload / brand tariff sync).

Brand-specific sync handlers still live in bootstrap setup during the strangler
transition; this package owns shared helpers and will absorb those handlers.
"""

from .helpers import coalesce_tariff_schedule

__all__ = ["coalesce_tariff_schedule"]
