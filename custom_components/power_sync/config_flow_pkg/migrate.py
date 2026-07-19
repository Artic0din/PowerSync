"""Config-entry schema migration helpers for nested options.

Nested schema intent
--------------------
Future options/data shape (additive; flat keys remain until callers migrate)::

    {
      "schema_version": 1,
      "provider": { ... electricity provider keys ... },
      "battery": { ... battery system / connection keys ... },
      "optimization": { ... smart opt keys ... },
      "ev": { ... EV / charger keys ... },
      "network": { ... network tariff / export envelope ... },
      "features": { ... curtailment, boost, chip mode, etc. ... },
    }

``migrate_options_to_nested`` is an identity transform for now: it stamps
``schema_version`` so ``async_migrate_entry`` in ``__init__.py`` can call it
later without a behavior change. Real key nesting lands in a later phase.
"""

from __future__ import annotations

from typing import Any

# Bump when nested grouping rules change.
NESTED_SCHEMA_VERSION = 1


def migrate_options_to_nested(
    data: dict[str, Any],
    options: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prepare entry data/options for the nested schema.

    Currently an identity transform that ensures ``schema_version`` is present
    on options. Safe to call from ``async_migrate_entry``.
    """
    new_data = dict(data)
    new_options = dict(options)
    new_options.setdefault("schema_version", NESTED_SCHEMA_VERSION)
    return new_data, new_options
