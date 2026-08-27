"""Regression tests for issue #30: the initial network-envelope
reconciliation kicked off during async_setup_entry must never block Home
Assistant bootstrap on an unbounded optimizer solve.

Following this repo's established pattern for its single giant __init__.py
(see tests/test_calendar_history_view_timeout.py and
tests/test_force_mode_controls.py), the real, shipped source is extracted
with ``ast`` and executed in a minimal namespace rather than reimplemented,
so these tests exercise the literal code that ships, not a description of
it. Nothing here exercises async_setup_entry/async_unload_entry directly
(impractical without a live HomeAssistant/ConfigEntry object graph, per
tests/test_unload_coordinator_teardown.py's docstring) — instead each of the
three pieces of the fix is extracted independently:

  1. ``_run_bounded_initial_network_envelope_reconciliation`` — the new
     module-level helper that wraps the first reconciliation call in
     ``asyncio.wait_for`` — is a standalone top-level async function, so it
     is extracted and driven directly (success/timeout/exception coverage).
  2. The two statements in async_setup_entry that create the task via that
     helper and store it in entry_data are extracted and driven with a fake
     hass (setup-side wiring coverage).
  3. The ``if initial_reconcile_task := ...`` block added to
     async_unload_entry is extracted and driven against a real asyncio.Task
     (unload cancellation coverage).

Reload coverage combines (2) and (3) to prove an unload+setup cycle leaves
exactly one live task behind, never zero orphaned-but-still-running ones and
never two.
"""

from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest


ROOT = Path(__file__).resolve().parent.parent
INIT_PATH = ROOT / "custom_components" / "power_sync" / "__init__.py"


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _find_assign_to_name(tree: ast.AST, name: str) -> ast.Assign:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == name:
                return node
    raise AssertionError(f"assignment to {name!r} not found")


def _find_assign_to_subscript_key(tree: ast.AST, key: str) -> ast.Assign:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Subscript):
                slice_node = target.slice
                if isinstance(slice_node, ast.Constant) and slice_node.value == key:
                    return node
    raise AssertionError(f"assignment to [...][{key!r}] not found")


def _find_walrus_if(tree: ast.AST, walrus_name: str) -> ast.If:
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.NamedExpr):
            target = node.test.target
            if isinstance(target, ast.Name) and target.id == walrus_name:
                return node
    raise AssertionError(f"if {walrus_name} := ... not found")


# ---------------------------------------------------------------------------
# 1. The bounded-wait helper itself
# ---------------------------------------------------------------------------


def _load_bounded_reconcile_helper():
    """Extract and exec the real
    ``_run_bounded_initial_network_envelope_reconciliation`` function."""
    source = INIT_PATH.read_text()
    tree = ast.parse(source)
    func_node = _find_function(tree, "_run_bounded_initial_network_envelope_reconciliation")
    module = ast.fix_missing_locations(ast.Module(body=[func_node], type_ignores=[]))
    namespace: dict[str, Any] = {
        "Any": Any,
        "Callable": Callable,
        "asyncio": asyncio,
        "logging": logging,
        # Real default constant/logger aren't needed since every test below
        # passes both explicitly, but the defaults are evaluated at
        # def-time so they must resolve to *something*.
        "_NETWORK_ENVELOPE_INITIAL_RECONCILE_TIMEOUT_SECONDS": 20.0,
        "_LOGGER": logging.getLogger("power_sync.test_stub"),
    }
    exec(compile(module, str(INIT_PATH), "exec"), namespace)
    return namespace["_run_bounded_initial_network_envelope_reconciliation"]


class _RecordingLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[Any, ...]] = []
        self.errors: list[tuple[Any, ...]] = []

    def warning(self, *args: Any) -> None:
        self.warnings.append(args)

    def error(self, *args: Any) -> None:
        self.errors.append(args)


def test_initial_reconcile_success_runs_to_completion_and_logs_nothing():
    async def scenario():
        helper = _load_bounded_reconcile_helper()
        logger = _RecordingLogger()
        calls: list[tuple[Any, Any]] = []

        async def reconcile(old, new):
            calls.append((old, new))

        old_envelope = object()
        new_envelope = object()
        await helper(
            reconcile,
            old_envelope,
            new_envelope,
            timeout_seconds=5.0,
            logger=logger,
        )

        assert calls == [(old_envelope, new_envelope)]
        assert logger.warnings == []
        assert logger.errors == []

    asyncio.run(scenario())


def test_initial_reconcile_timeout_is_bounded_logs_once_and_grants_nothing():
    """A stalled solve must not block past the deadline, must log exactly
    one diagnostic, and must leave the (fake) guard's deny-by-default state
    untouched -- the wrapper never itself calls anything that could grant
    export authority."""

    async def scenario():
        helper = _load_bounded_reconcile_helper()
        logger = _RecordingLogger()
        stuck = asyncio.Event()
        approve_calls: list[Any] = []

        class _FakeGuard:
            approved_limit_w = 0.0  # deny-by-default

            def approve_reoptimized_snapshot(self, version):
                approve_calls.append(version)
                self.approved_limit_w = 9_999.0
                return True

        guard = _FakeGuard()

        async def reconcile(old, new):
            # Simulate a hung LP/optimizer solve inside the real
            # _network_envelope_changed's forced-optimization branch. Only
            # reaches guard.approve_reoptimized_snapshot() *after* the
            # await, which never happens because wait_for cancels us first.
            await stuck.wait()
            guard.approve_reoptimized_snapshot(1)

        started = asyncio.get_event_loop().time()
        await helper(
            reconcile,
            object(),
            object(),
            timeout_seconds=0.05,
            logger=logger,
        )
        elapsed = asyncio.get_event_loop().time() - started

        assert elapsed < 2.0, "bounded reconciliation must not wait for the stalled solve"
        assert len(logger.warnings) == 1, "must log exactly one timeout diagnostic"
        assert logger.errors == []
        assert approve_calls == [], "timeout must never grant export authority"
        assert guard.approved_limit_w == 0.0, "deny-by-default must survive a timeout"

        # release the stalled coroutine so nothing lingers after the test
        stuck.set()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_initial_reconcile_exception_does_not_propagate_and_grants_nothing():
    """An exception raised while reconciling (e.g. optimizer solve raising)
    must not escape and blow up async_setup_entry, and must not grant
    export authority."""

    async def scenario():
        helper = _load_bounded_reconcile_helper()
        logger = _RecordingLogger()
        approve_calls: list[Any] = []

        async def reconcile(old, new):
            approve_calls.append("reached")
            raise RuntimeError("optimizer solve blew up")

        # Must not raise.
        await helper(
            reconcile,
            object(),
            object(),
            timeout_seconds=5.0,
            logger=logger,
        )

        assert approve_calls == ["reached"]
        assert logger.warnings == []
        assert len(logger.errors) == 1, "must log exactly one error diagnostic"

    asyncio.run(scenario())


def test_initial_reconcile_cancellation_propagates_instead_of_being_swallowed():
    """If the wrapping task is cancelled (e.g. during unload), the wrapper
    must not swallow CancelledError as a generic exception."""

    async def scenario():
        helper = _load_bounded_reconcile_helper()
        logger = _RecordingLogger()
        stuck = asyncio.Event()

        async def reconcile(old, new):
            await stuck.wait()

        task = asyncio.ensure_future(
            helper(reconcile, object(), object(), timeout_seconds=30.0, logger=logger)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert logger.warnings == []
        assert logger.errors == []

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 2. Setup-side wiring: task creation + storage
# ---------------------------------------------------------------------------


def _load_setup_task_creation():
    source = INIT_PATH.read_text()
    tree = ast.parse(source)
    setup_func = _find_function(tree, "async_setup_entry")
    create_assign = _find_assign_to_name(setup_func, "network_envelope_initial_reconcile_task")
    store_assign = _find_assign_to_subscript_key(
        setup_func, "network_envelope_initial_reconcile_task"
    )

    wrapper = ast.parse(
        "async def _setup_creates_and_stores_initial_reconcile_task("
        "hass, entry, DOMAIN, _network_envelope_changed, NetworkExportEnvelope, "
        "network_envelope_manager, _run_bounded_initial_network_envelope_reconciliation"
        "):\n    pass\n"
    ).body[0]
    wrapper.body = [create_assign, store_assign]
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    namespace: dict[str, Any] = {}
    exec(compile(module, str(INIT_PATH), "exec"), namespace)
    return namespace["_setup_creates_and_stores_initial_reconcile_task"]


class _FakeHass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {"power_sync": {"entry-1": {}}}

    def async_create_task(self, coro):
        return asyncio.ensure_future(coro)


def test_setup_creates_task_via_bounded_helper_and_stores_it_in_entry_data():
    async def scenario():
        setup_func = _load_setup_task_creation()
        hass = _FakeHass()
        entry = SimpleNamespace(entry_id="entry-1")
        sentinel_old = object()
        sentinel_snapshot = object()
        recorded: list[tuple[Any, Any, Any]] = []

        async def fake_bounded_helper(reconcile, old, new):
            recorded.append((reconcile, old, new))

        async def reconcile_stub(old, new):
            return None

        manager = SimpleNamespace(snapshot=sentinel_snapshot)

        await setup_func(
            hass,
            entry,
            "power_sync",
            reconcile_stub,
            lambda: sentinel_old,
            manager,
            fake_bounded_helper,
        )
        await asyncio.sleep(0)

        entry_data = hass.data["power_sync"]["entry-1"]
        task = entry_data.get("network_envelope_initial_reconcile_task")
        assert task is not None, "setup must store the created task in entry_data"
        assert recorded == [(reconcile_stub, sentinel_old, sentinel_snapshot)], (
            "setup must create the task via the bounded helper, not the raw "
            "unbounded reconcile call, passing a fresh deny-only envelope as "
            "old and the manager's current snapshot as new"
        )

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 3. Unload-side wiring: cancellation
# ---------------------------------------------------------------------------


def _load_unload_cancel_block():
    source = INIT_PATH.read_text()
    tree = ast.parse(source)
    unload_func = _find_function(tree, "async_unload_entry")
    if_node = _find_walrus_if(unload_func, "initial_reconcile_task")

    wrapper = ast.parse(
        "async def _unload_cancels_initial_reconcile_task(entry_data):\n    pass\n"
    ).body[0]
    wrapper.body = [if_node]
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    namespace: dict[str, Any] = {
        "_LOGGER": logging.getLogger("power_sync.test_stub"),
    }
    exec(compile(module, str(INIT_PATH), "exec"), namespace)
    return namespace["_unload_cancels_initial_reconcile_task"]


def test_unload_cancels_a_still_pending_initial_reconcile_task():
    async def scenario():
        unload_func = _load_unload_cancel_block()
        stuck = asyncio.Event()

        async def stalled():
            await stuck.wait()

        task = asyncio.ensure_future(stalled())
        await asyncio.sleep(0)
        assert not task.done()

        entry_data: dict[str, Any] = {"network_envelope_initial_reconcile_task": task}

        await unload_func(entry_data)  # must not raise

        assert entry_data["network_envelope_initial_reconcile_task"] is None
        for _ in range(10):
            if task.done():
                break
            await asyncio.sleep(0)
        assert task.cancelled()

    asyncio.run(scenario())


def test_unload_is_a_noop_when_reconcile_task_already_finished():
    async def scenario():
        unload_func = _load_unload_cancel_block()

        async def quick():
            return "done"

        task = asyncio.ensure_future(quick())
        await task

        entry_data: dict[str, Any] = {"network_envelope_initial_reconcile_task": task}

        await unload_func(entry_data)  # must not raise, must not re-cancel a done task

        assert entry_data["network_envelope_initial_reconcile_task"] is None

    asyncio.run(scenario())


def test_unload_is_a_noop_when_no_reconcile_task_was_ever_created():
    async def scenario():
        unload_func = _load_unload_cancel_block()
        entry_data: dict[str, Any] = {}

        await unload_func(entry_data)  # must not raise

        assert "network_envelope_initial_reconcile_task" not in entry_data

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 4. Reload: setup + unload combined
# ---------------------------------------------------------------------------


def test_reload_cycle_leaves_no_orphaned_or_duplicate_reconcile_task():
    """Unload must cancel the pending task from the first setup before a
    second setup (reload) creates a new one, and the two tasks must never
    coexist as separate live objects under the same entry_data key."""

    async def scenario():
        setup_func = _load_setup_task_creation()
        unload_func = _load_unload_cancel_block()
        bounded_helper = _load_bounded_reconcile_helper()

        hass = _FakeHass()
        entry = SimpleNamespace(entry_id="entry-1")
        manager = SimpleNamespace(snapshot=object())

        # ---- first setup (e.g. HA startup) ----
        first_stuck = asyncio.Event()

        async def first_reconcile(old, new):
            await first_stuck.wait()

        await setup_func(
            hass, entry, "power_sync", first_reconcile, object, manager, bounded_helper
        )
        await asyncio.sleep(0)

        entry_data = hass.data["power_sync"]["entry-1"]
        first_task = entry_data["network_envelope_initial_reconcile_task"]
        assert first_task is not None
        assert not first_task.done()

        # ---- unload ----
        await unload_func(entry_data)
        assert entry_data["network_envelope_initial_reconcile_task"] is None
        # Cancellation has to propagate through wait_for -> Event.wait(),
        # which takes more than one event-loop turn to settle.
        for _ in range(10):
            if first_task.done():
                break
            await asyncio.sleep(0)
        assert first_task.cancelled(), "unload must cancel the orphaned first-setup task"

        # ---- reload: setup runs again against the same entry_data dict ----
        second_stuck = asyncio.Event()

        async def second_reconcile(old, new):
            await second_stuck.wait()

        await setup_func(
            hass, entry, "power_sync", second_reconcile, object, manager, bounded_helper
        )
        await asyncio.sleep(0)

        second_task = entry_data["network_envelope_initial_reconcile_task"]
        assert second_task is not None
        assert second_task is not first_task, "reload must not reuse a cancelled task object"
        assert not second_task.done()

        # cleanup: release the second task so nothing lingers after the test
        second_stuck.set()
        await second_task

    asyncio.run(scenario())
