"""Setup / unload / migrate orchestration entrypoints (Phase 7).

PRACTICAL NOTE: the full ``async_setup``, ``async_setup_entry``,
``async_unload_entry``, and ``async_migrate_entry`` implementations still live
in ``custom_components.power_sync.__init__`` (they are large and tightly
coupled). This module documents the target surface and re-exports lifecycle
helpers used during entry setup.

Call sites should prefer:

- ``bootstrap.lifecycle.attach_runtime`` for EntryRuntime dual-write
- eventual thin wrappers here once bodies are migrated out of ``__init__.py``
"""

from __future__ import annotations

from .lifecycle import attach_runtime, build_entry_runtime, register_battery_port

__all__ = [
    "attach_runtime",
    "build_entry_runtime",
    "register_battery_port",
]
