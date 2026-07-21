# AGENTS.md — PowerSync

PowerSync is a Home Assistant custom integration (`custom_components/power_sync`) for battery energy management with dynamic electricity pricing. Primary community support is Discord; GitHub Issues are for tracked bugs and enhancements.

## Layout

- Integration code: `custom_components/power_sync/`
- Unit tests: `tests/` (pytest, Python 3.12)
- Blueprints: `blueprints/`
- Dashboard YAML: `HA Dashboard/`
- Docs/wiki notes: `docs/`

## Verification

Before opening a PR that changes code:

```bash
python3.12 -m pytest tests/ -q
```

CI also runs HACS validation and hassfest (`.github/workflows/validate.yml`). Prefer small, focused changes. Do not change public config-flow options, entity unique IDs, or service schemas without a clear migration path and maintainer approval.

## Conventions

- Keep changes surgical; one concern per PR.
- Match existing patterns in nearby modules (battery controllers, coordinators, pricing providers).
- Avoid new runtime dependencies unless discussed in an issue first.
- Do not commit secrets, Tesla private keys, or `.env` files.
- Optimizer / Smart Optimization code is under active development — treat schedule and control changes carefully and add or update tests when behavior changes.

## Repo Assist

When Repo Assist runs here, prefer issue triage, investigation, and minimal fixes aligned with the open backlog. Use labels conservatively. Draft PRs only for clear, testable fixes.
