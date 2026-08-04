# PowerSync agent instructions

## Purpose

PowerSync is a Python Home Assistant custom integration for electricity pricing, battery and inverter control, smart optimisation, EV charging, and related energy automations.
It supports multiple external providers and hardware families, so changes can affect real household energy control.
Its core value includes the built-in Smart Optimization LP engine (HiGHS solver via `highspy`) that computes charge/discharge schedules from prices, solar, and load.

## Sources of truth

- `custom_components/power_sync/` is the integration implementation and contract source.
- `custom_components/power_sync/manifest.json`, `hacs.json`, and `.github/workflows/validate.yml` define Home Assistant and HACS metadata validation.
- `tests/`, `pytest.ini`, and `conftest.py` define local regression coverage.
- `README.md`, `docs/`, and the repository wiki define supported setup and behaviour.

## Setup and validation

Prefer the repository Python 3.12 virtualenv at `.venv/` when present (`tzdata` is required for tariff imports).

```bash
.venv/bin/python -m pytest
# or, with an activated/configured environment:
python3 -m pytest
```

Run focused tests for the changed controller, provider, optimizer, or entity first, then the full suite.
For metadata or integration-structure changes, ensure the HACS and Hassfest CI jobs also pass.
There is no dedicated Python linter; local static checks are pytest plus
`.venv/bin/python -m compileall custom_components/power_sync`. hassfest is not pip-installable, so run it via GitHub Actions.

Known pre-existing pytest failures are not environment breakage and must not be "fixed" as part of setup: a cluster of tests (including `tests/test_sungrow_sh_controller.py`, `tests/test_ev_vehicle_status.py`, `tests/test_grid_charge_soc_cap.py`, and `tests/test_reserve_floor_scoping.py`) hand-stub `homeassistant` / `power_sync` modules in `sys.modules` and those stubs have drifted. CI runs HACS + hassfest, not pytest. Follow-up tracking is issue #60.

## Boundaries

- Treat battery, inverter, grid, tariff, scheduling, reserve, and EV control as safety-critical logic; write exact tests before changing behaviour.
- Verify entity IDs, units, sign conventions, service names, provider fields, and hardware contracts before coding.
- Preserve fail-safe behaviour and never turn a read/observe path into a write/control path without explicit scope and tests.
- Do not test control commands against a live Home Assistant instance or real hardware unless explicitly authorised.
- Keep changes focused and do not relocate application code unless explicitly requested.
- Preserve upstream compatibility, licensing, and repository contribution requirements.
- Update `README.md`, `docs/`, or `CHANGELOG.md` with code or configuration changes and include exact validation evidence in the pull-request body.

## Work tracking

- GitHub Issues are canonical for planned, multi-session, or backlog work; small one-PR fixes do not require an issue.
- The user-level `Development` Project is a dashboard, while issues, pull requests, reviews, and CI remain authoritative.
- For issue-backed work, use one issue per branch and pull request, include the issue number in the branch name, and add `Fixes #123` to the pull-request body.
- Keep Project status at `Todo` before work, `In Progress` during implementation or review, and `Done` only after closure or merge.
- Update issue checklists only for verified work; checklist completion is never a merge gate.

## Knowledge completion

Before creating durable project knowledge, search the `ryan-knowledge` Basic Memory project.
Only add or update a note in `AI Memory/` when it is reusable and verified against code, tests, repository documentation, live readback, or another primary source.
Never store credentials or environment-specific secrets, and never let a knowledge note override repository files.

## Cursor Cloud specific instructions

The startup/update script provisions a Python 3.12 virtualenv at `.venv/` in the repo root and
installs `pytest`, `tzdata`, `homeassistant`, and the runtime `requirements` from
`manifest.json`. Run tools via `.venv/bin/...` (or activate the venv). `.venv/` is gitignored.

### Running the app (Home Assistant) for manual / e2e testing

Home Assistant config is intentionally NOT stored in this repo. To run PowerSync inside a live
HA instance:

- Create a config dir and symlink the integration into it:
  `mkdir -p ~/ha_config/custom_components && ln -sfn "$PWD/custom_components/power_sync" ~/ha_config/custom_components/power_sync`
- Do NOT use `default_config:` in `~/ha_config/configuration.yaml`. Several of its optional
  dependencies (`go2rtc` binary, `pyspeex-noise`, `aiodiscover`, `turbojpeg`) are missing in this
  headless VM and abort startup. Use a minimal config instead: `frontend:`, `config:`, `api:`,
  `recorder:`, `history:`. PowerSync only needs frontend + config (Devices & Services UI) + api.
- Start it long-running (background/tmux): `.venv/bin/hass -c ~/ha_config`. UI at
  `http://localhost:8123` (first run requires one-time onboarding to create an owner account).
- No-credentials setup path: in Add Integration → PowerSync, choose provider **Other** → **Flat
  Rate** tariff → battery **Custom / external controller**. That path needs 5 `sensor`-domain
  entities (battery level, battery power, grid power, solar power, load power); define them as
  `template:` sensors in `configuration.yaml` to exercise the flow with no external API keys.
- The Smart Optimization engine runs every 5 minutes and logs `Optimization complete (highs, …)`.
  Without a solar forecast integration (Solcast / Open-Meteo) it logs a benign "Solar forecast
  not available" warning and falls back to a zero-solar forecast — expected in this environment.
