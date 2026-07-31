# PowerSync agent instructions

## Purpose

PowerSync is a Python Home Assistant custom integration for electricity pricing, battery and inverter control, smart optimisation, EV charging, and related energy automations.
It supports multiple external providers and hardware families, so changes can affect real household energy control.

## Sources of truth

- `custom_components/power_sync/` is the integration implementation and contract source.
- `custom_components/power_sync/manifest.json`, `hacs.json`, and `.github/workflows/validate.yml` define Home Assistant and HACS metadata validation.
- `tests/`, `pytest.ini`, and `conftest.py` define local regression coverage.
- `README.md`, `docs/`, and the repository wiki define supported setup and behaviour.

## Setup and validation

Use the repository's configured Python version and existing environment.

```bash
python3 -m pytest
```

Run focused tests for the changed controller, provider, optimizer, or entity first, then the full suite.
For metadata or integration-structure changes, ensure the HACS and Hassfest CI jobs also pass.

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
