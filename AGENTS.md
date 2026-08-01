# PowerSync — agent instructions

PowerSync is a Home Assistant custom integration (`custom_components/power_sync`, Python 3.12)
for intelligent battery energy management. Its core value is the built-in Smart Optimization
LP engine (HiGHS solver via `highspy`) that computes charge/discharge schedules from prices,
solar, and load.

## Primary sources of truth

- `custom_components/power_sync/manifest.json` — domain, version, and runtime `requirements`.
- `custom_components/power_sync/` — integration code (config flow, coordinator, optimization engine, per-vendor adapters).
- `README.md` — supported systems, features, and installation.
- `.github/workflows/validate.yml` — CI gates (HACS + hassfest validation).
- `tests/` — pytest suite (self-stubs Home Assistant; see caveats below).

## Cursor Cloud specific instructions

The startup/update script provisions a Python 3.12 virtualenv at `.venv/` in the repo root and
installs `pytest`, `tzdata`, `homeassistant`, and the runtime `requirements` from
`manifest.json`. Run tools via `.venv/bin/...` (or activate the venv). `.venv/` is gitignored.

### Tests

- Run: `.venv/bin/python -m pytest` (≈2 min; ~2218 pass).
- Known pre-existing failures (NOT environment breakage, do not "fix" as part of setup): a
  cluster of ~80 tests in a few files — `tests/test_sungrow_sh_controller.py`,
  `tests/test_ev_vehicle_status.py`, `tests/test_grid_charge_soc_cap.py`,
  `tests/test_reserve_floor_scoping.py`, and a handful of others — fail or error because those
  files hand-stub `homeassistant` / `power_sync.const` / `power_sync.optimization.coordinator`
  in `sys.modules`, and the stubs have drifted out of sync with the current code (missing names
  such as `ConfigEntryAuthFailed`, `POWERSYNC_AUTH_ME_URL`, `sigenergy_capped_optimizer_limit_w`).
  CI never runs pytest (only HACS + hassfest), so this drift went unnoticed. Installing the real
  `homeassistant` does not change the outcome because these files replace it in `sys.modules`.
- `tzdata` is required: the `aemo-to-tariff` dependency resolves `ZoneInfo("Australia/ACT")` at
  import time, so without `tzdata` the tariff tests fail to collect.

### Lint / validation

There is no dedicated Python linter configured. Local static checks are pytest plus
`.venv/bin/python -m compileall custom_components/power_sync`. The authoritative validation
gates are HACS and hassfest, which run in CI (`.github/workflows/validate.yml`). hassfest is not
pip-installable (it lives in the Home Assistant core repo), so run it via GitHub Actions rather
than locally.

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
