# AGENTS.md — PowerSync (fork)

Fork of `bolagnaise/PowerSync` (upstream); `origin` is `Artic0din/PowerSync`.
HA custom component (HACS) at `custom_components/power_sync/`. Min HA 2024.8.0 (`hacs.json`).

## Upstream PR discipline

- Small, focused PRs against upstream `main` — one fix or one test concern per PR.
- Never reformat: upstream has no ruff/black/flake8/pre-commit config. Touch only the lines the change needs.
- Never add fork-only CI or edit upstream workflows (`validate.yml` = HACS + hassfest, `release.yml`, `pages.yml`, `sponsors.yml` are upstream-owned).
- Never bump `manifest.json` version or touch `RELEASE_NOTES.md` in a PR — that is upstream's release process.
- EV charging code (`automations/ev_charging_planner.py`, `ev_charging_session.py`, etc.) IS in upstream scope: other users depend on it.
  Ryan's personal HA delegates EV charging to EVCC, but that is personal config, not project policy — EV bug fixes go upstream (carl-decisions powersync-001).

## Build / test

- Tests: `pytest` from repo root (`pytest.ini`, `testpaths = tests`).
- No `requirements.txt` — Python deps are declared in `custom_components/power_sync/manifest.json` under `requirements`.
- CI on push/PR to `main`: HACS validation + hassfest (`.github/workflows/validate.yml`).

## Domain conventions

- Services live in `custom_components/power_sync/services.yaml`:
  `force_charge` = autonomous mode + free import tariff (maximise grid charging);
  `force_discharge` = autonomous mode + high export tariff (maximise export);
  `restore_normal` = restore saved tariff, or trigger Amber sync for Amber users.
- Inverter drivers in `custom_components/power_sync/inverters/` — per-vendor modules with a shared `base.py`. Several vendors span a companion file (`foxess.py`+`foxess_entity.py`, `goodwe.py`+`goodwe_battery.py`, `solax.py`+`solax_battery.py`, `fronius.py`+`fronius_reserva.py`, `sungrow.py`+`sungrow_sh.py`) — update the companion when modifying a driver.
- Vendor-specific gotchas are documented in `docs/wiki/` (AlphaESS, GoodWe, Sigenergy, EV-Charging-Refactor) — read before touching a vendor driver.