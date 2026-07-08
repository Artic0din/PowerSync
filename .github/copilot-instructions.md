# Copilot Cloud Agent Instructions for `PowerSync`

## Repository at a glance
- This is a **Home Assistant custom integration** implemented in Python.
- Primary integration code is in `custom_components/power_sync/`.
- Tests are in `tests/` and use `pytest`.
- CI validation in this repo currently focuses on:
  - HACS metadata validation (`.github/workflows/validate.yml`)
  - Home Assistant Hassfest validation (`.github/workflows/validate.yml`)

## High-value directories
- `custom_components/power_sync/`: runtime integration code (config flow, coordinators, entities, providers, inverter adapters, optimizer, frontend assets).
- `custom_components/power_sync/optimization/`: scheduling/optimizer logic and coordinator flow.
- `custom_components/power_sync/inverters/`: vendor-specific battery/inverter controller implementations.
- `custom_components/power_sync/powerwall_local/`: Tesla Powerwall local/protobuf path.
- `tests/`: extensive unit coverage; keep changes scoped and add/adjust targeted tests when behavior changes.
- `docs/`: wiki-style documentation and GitHub Pages site content.
- `.github/workflows/`: CI/release/sponsors/pages automation.

## How to work efficiently in this repo
1. Keep changes **surgical** and localized to the feature area (this codebase is large and provider-specific behavior is easy to regress).
2. Before editing, identify the specific provider/controller path impacted (for example inverter adapters vs optimization logic).
3. When changing behavior, update or add focused tests in `tests/` near the affected subsystem.
4. Prefer targeted validation before broad runs:
   - Focused test: `python3 -m pytest tests/<target_test_file>.py`
   - Broader run (when needed): `python3 -m pytest tests`
5. If touching integration metadata/release behavior, check:
   - `custom_components/power_sync/manifest.json`
   - `.github/workflows/release.yml`
6. Avoid broad refactors unless explicitly requested.

## Validation expectations
- Minimum for code changes: run relevant `pytest` tests for touched behavior.
- For metadata/packaging changes, ensure HACS/Hassfest assumptions remain valid.
- For docs-only changes, tests are usually not required.

## Release/process notes
- Version is sourced from `custom_components/power_sync/manifest.json`.
- Release automation is triggered by manifest version bumps.
- Optional hand-written `RELEASE_NOTES.md` is consumed only when marker/version checks pass in release workflow.

## Common pitfalls
- Many modules are provider-specific; similar names can map to different control paths.
- Regression risk is highest in optimization scheduling and inverter control adapters; always prefer targeted tests.
- Python version compatibility matters for tests (`conftest.py` enforces Python >= 3.10).

## Errors encountered during onboarding and workarounds
- Expected contributor guidance files `AGENTS.md` and `CONTRIBUTING.md` were not present at repository root.
  - Workaround: used `README.md`, workflow files in `.github/workflows/`, and in-repo test/config files (`pytest.ini`, `conftest.py`) as authoritative sources for onboarding guidance.
