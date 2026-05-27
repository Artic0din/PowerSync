# PowerSync — Agent Rules

Fork of `bolagnaise/PowerSync`. HACS integration for battery optimisation.

## Stack

- Python 3.12+
- Home Assistant 2025.x+
- LP optimiser (built-in)
- pytest + pytest-asyncio + pytest-homeassistant-custom-component
- Real users via TestFlight (iOS) + Google Play (Android)

## Project context

PowerSync provides intelligent battery energy management for Home Assistant. Supports Tesla Powerwall, FoxESS, Sigenergy, GoodWe, Sungrow SH, AlphaESS, ESY Sunhome, Solax Hybrid, SAJ H2/HS2, Neovolt/Bytewatt, SolarEdge. AC-coupled solar inverter curtailment (Fronius, Sungrow SG/SH, Enphase, FoxESS, Huawei, GoodWe, Zeversolar, Solax, Sigenergy, AlphaESS). Electricity providers: Amber, Localvolts, Flow Power/AEMO, GloBird/AEMO VPP, Octopus, EPEX, NZ TOU.

Mobile app companion (iOS/Android) talks to HA via long-lived access token.

## Layout

```
custom_components/power_sync/
├── __init__.py
├── manifest.json
├── config_flow.py
├── const.py
├── services.yaml
├── strings.json
├── translations/en.json
├── optimization/         # LP solver, EV coordinator, action plan
├── providers/            # Amber, Localvolts, Octopus, EPEX, AEMO, GloBird
├── batteries/            # Per-vendor battery integrations
├── curtailment/          # AC-coupled inverter curtailment
├── views/                # HomeAssistantView endpoints (mobile app API)
├── sensor.py
├── binary_sensor.py
├── switch.py
├── number.py
├── select.py
└── tests/

docs/audits/              # Audit artefacts (PR #1 baseline)
blueprints/               # HA automation blueprints
HA Dashboard/             # Pre-built dashboard YAML
```

## Build, test, lint

```bash
pip install -r requirements.txt 2>/dev/null || true
pip install ruff pytest pytest-asyncio pytest-cov pytest-homeassistant-custom-component
ruff check .                          # advisory until broad-except remediation lands
pytest --cov=custom_components/power_sync -x
gitleaks detect --source . --no-git
```

## PR size discipline

- **Target ≤200 lines, hard ceiling 400 lines.** Above 400, split.
- Empirical basis: SmartBear/Cisco (2006), Propel Code (50k PRs, 2024), SWE-PRBench (arXiv:2603.26130). Defect detection collapses above 400 LOC for humans AND AI reviewers.
- Codex's P0 detection rate drops on large diffs — attention dilution.
- Audit remediation MUST batch by module, never fix-all in one PR.
- Exemptions: dependabot, generated files, translations.

## Upstream relationship

This is a fork. Upstream is `bolagnaise/PowerSync`. Goals:

1. Don't break upstream sync compatibility
2. Fix real bugs from audit, propose them upstream as small focused PRs (≤400 lines)
3. Build contribution credibility through concrete fixes before proposing process changes

**Never propose fork-CI scaffolding upstream until at least 3-5 concrete bug-fix PRs have merged successfully.**

## Branch model

- `main` — tracks upstream 1:1. Fast-forward only from upstream.
- `sync/upstream-YYYYMMDD` — temporary upstream pulls. Advisory CI only.
- `feat/*`, `fix/*`, `chore/*`, `docs/*` — Ryan's work. Strict CI. Becomes upstream PR.
- `audit/*` — fork-only hardening from audit phases. Strict CI. NOT proposed upstream initially.

## Review guidelines

### P0 — drop everything to fix

- Blocking I/O (`requests`, `time.sleep`, `asyncio.sleep ≥60s`, file ops without `aiofiles`) in async paths
- Missing `await` on coroutine call
- Hardcoded token, API key, secret in any file
- API key, token, or PII in log statement
- New `HomeAssistantView` without `requires_auth = True`
- New service registration without `vol.Schema` validation
- Storage `from_dict()` without explicit HA-timezone date (no `date.today()` fallback)
- State restore loading without validating storage version
- Mobile app endpoint returning unsanitised user data
- LP optimiser change without edge-case test (negative prices, zero solar, empty schedule)

### P1 — urgent, fix this cycle

- New external HTTP call without timeout
- New `try/except` swallowing exception without specific type AND `# noqa: reason` comment
- HA deprecation warning introduced by patch
- New public service handler without test
- User-facing string added outside `strings.json` / `translations/en.json`
- New broad-except (`except Exception` or bare `except`) without `# noqa` justification
- Battery vendor integration change without round-trip test
- Provider pricing change without rate-curve validation
- Modbus write without retry/error handling
- Config flow change without `test_config_flow.py` update

### P2 — fix eventually, do not block merge

- Missing docstring on public function
- Magic number that would be clearer as a named constant
- Test missing assertion message
- Code duplication < 3 occurrences

### P3 — do not flag on GitHub

- Docstring style nits (ruff handles)
- Typo in comment
- Import ordering (ruff handles)
- Line length (ruff handles)

## Known debt — do NOT flag, audit-tracked

These exist in the codebase as of PR #1 baseline. Codex should NOT re-flag:

- 938 broad-except clauses + 4 bare except (Phase 3 remediation)
- 74 silent exception swallows (Phase 3 remediation)
- 4 blocking `asyncio.sleep ≥60s` sites — Phase 2:
  - `__init__.py:16814`
  - `optimization/ev_coordinator.py:218,224`
  - `optimization/coordinator.py:2025`
- 30/30 services without `vol.Schema` (Phase 2 remediation, batched by domain)
- 29.1% conventional-commits historical compliance (Ryan's new commits enforce; upstream history doesn't)
- 22 fix-of-fix commits in history (audit finding H14)
- `esy_sunhome` custom-only dependency (M14)

### Repo-config debt (fork settings, NOT code)

- `validate-hacs` red on every PR — fork repo `Artic0din/PowerSync` missing GitHub topics AND has Issues disabled (HACS requires both). Fix in repo Settings → General → Features (enable Issues) and Settings → Topics (add e.g. `hacs`, `home-assistant`, `battery`, `energy`). One-time settings change, unblocks all future PRs.

## High-risk paths

CODEOWNERS gates ONLY:

- `.github/` — workflow changes can lock repo out of CI
- `custom_components/power_sync/manifest.json` — version bumps affect all HACS users

Cost-calc accuracy, authentication, and LP optimiser correctness enforced via P0 review rules above, not CODEOWNERS.

Fresh-eyes review required (you the next morning, regardless of agent verdict):

- Any change to `optimization/coordinator.py` LP solver core
- Any change to `views/` authentication or authorization
- Any change to battery vendor write paths (force charge/discharge, backup reserve)
- Any change to mobile app API contract
- Any new electricity provider integration

## Auto-merge

**OFF.** Real users via TestFlight + Google Play. Manual merge always. The release IS the rollback boundary but the blast radius is too large for auto-merge.

## Mobile app considerations

- API contract changes break mobile app users until they update
- Long-lived access token is the auth mechanism — never log it, never embed in responses
- Mobile app reads from REST + WebSocket; both surfaces need backwards compatibility on breaking changes
- Version bumps in `manifest.json` should be coordinated with mobile app release cadence

## graphify (if present)

If `graphify-out/` exists at repo root:
- Read `graphify-out/GRAPH_REPORT.md` before architecture questions
- Navigate `graphify-out/wiki/index.md` instead of raw files
- Run `graphify update .` after modifying code (AST-only, no API cost)
