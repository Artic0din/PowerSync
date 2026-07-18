# AGENTS.md

## Cursor Cloud specific instructions

PowerSync is a Home Assistant custom integration (a Python package under
`custom_components/power_sync/`), not a standalone web/CLI app. There is no
server to boot; the code runs inside Home Assistant. The startup update script
already installs all dependencies, so the notes below are the non-obvious
caveats for developing/testing here.

### Python / dependencies
- Use `python3.12` (repo pins `3.12` in `.python-version`). Always invoke tests
  as `python3.12 -m pytest` — the root `conftest.py` hard-fails on interpreters
  older than 3.10.
- There is no `requirements.txt`. Runtime deps live in
  `custom_components/power_sync/manifest.json` (`requirements`). Tests also need
  `pytest` and `tzdata` (the base image has no IANA tz data, and
  `aemo-to-tariff` imports `ZoneInfo('Australia/ACT')` at module load).
- The `homeassistant` package is NOT required: tests inject their own HA stubs
  via `sys.modules`.

### Testing (non-obvious gotcha)
- CI does not run pytest (only hassfest + HACS validation in
  `.github/workflows/validate.yml`); local pytest is the only functional gate.
- Prefer per-file / narrow runs, e.g. `python3.12 -m pytest tests/<file>.py`.
  Every test file passes in isolation.
- The full-suite run (`python3.12 -m pytest`) currently hits a pre-existing
  test-isolation bug: an earlier test leaves a bare `pymodbus` stub (with
  `__spec__ = None`) in `sys.modules`, and later `test_sungrow_sh_controller.py`
  calls `importlib.util.find_spec("pymodbus")`, which then raises
  `ValueError: pymodbus.__spec__ is None` and cascades into ~57 failures. This
  is an ordering artifact in the test harness, NOT an environment problem, and
  installing real `pymodbus` does not fix it (it is optional and stubbed). Don't
  chase it as an env issue — validate changes with per-file runs.

### Running the core optimizer standalone
- The core product logic (LP battery optimizer using the HiGHS solver via
  `highspy`) can be exercised without Home Assistant by stubbing the minimal HA
  surface in `sys.modules` and importing
  `power_sync.optimization.battery_optimizer` (see the stub pattern in
  `scripts/benchmark_lp_optimizer.py`). Availability flag is
  `battery_optimizer.HIGHS_AVAILABLE`.
- Note: the committed `scripts/benchmark_lp_optimizer.py` is stale — it checks a
  `SCIPY_AVAILABLE` attribute that the module no longer defines and will raise
  `AttributeError`. The optimizer uses `highspy`, not scipy.
