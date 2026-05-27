# PowerSync — Fork-Specific Agent Rules

Fork of `bolagnaise/PowerSync`. HACS integration for battery optimisation.

## Stack

- Python 3.12+
- Home Assistant 2025.x+
- LP optimiser (built-in)
- pytest-homeassistant-custom-component

## Build, test, lint

- Install: `pip install -r requirements.txt && pip install ruff pytest pytest-asyncio pytest-cov pytest-homeassistant-custom-component`
- Lint: `ruff check .` (advisory until broad-except remediation lands)
- Tests: `pytest --cov=custom_components/power_sync`

## Upstream relationship

This is a fork. Upstream is `bolagnaise/PowerSync`. Goals:
1. Don't break upstream sync compatibility
2. Fix real bugs from audit, propose them upstream as small focused PRs
3. Stay invisible to upstream maintainers until contribution credibility is built

**Never propose fork-CI scaffolding upstream until at least 3-5 concrete bug-fix PRs have merged successfully.**

## Branch model

- `main` — tracks upstream 1:1
- `sync/upstream-YYYYMMDD` — temporary upstream pulls. Advisory CI only.
- `feat/*`, `fix/*`, `chore/*` — Ryan's work. Strict CI. Becomes upstream PR.
- `audit/*` — fork-only hardening. Strict CI. NOT proposed upstream initially.

## Review guidelines

### P0 — drop everything to fix

- Blocking I/O (`requests`, `time.sleep`, `asyncio.sleep ≥60s`, file ops without `aiofiles`) in async paths
- Missing `await` on coroutine call
- Hardcoded token, API key, secret in any file
- API key, token, or PII in log statement
- New `HomeAssistantView` without `requires_auth = True`
- New service registration without `vol.Schema` validation
- Storage `from_dict()` without explicit HA-timezone date

### P1 — urgent, fix this cycle

- New external HTTP call without timeout
- New `try/except` that swallows exception without specific exception type AND `# noqa: reason` comment
- HA deprecation warning introduced
- New public service handler without test
- User-facing string added outside `strings.json` / `translations/en.json`
- New broad-except (`except Exception` or bare `except`) without `# noqa` justification

### P2 — fix eventually, do not block merge

- Missing docstring on public function
- Magic number that would be clearer as a named constant
- Test missing assertion message

### P3 — do not flag on GitHub

- Docstring style nits (ruff handles)
- Typo in comment
- Import ordering (ruff handles)

## Known debt — do NOT flag, audit-tracked

These exist in the codebase as of PR #1 baseline. Codex should NOT re-flag:
- 938 broad-except clauses (Phase 3 remediation)
- 74 silent exception swallows (Phase 3 remediation)
- 4 blocking `asyncio.sleep ≥60s` sites at:
  - `__init__.py:16814`
  - `optimization/ev_coordinator.py:218,224`
  - `optimization/coordinator.py:2025`
  (Phase 2 remediation, each becomes a separate upstream PR)
- 30/30 services without `vol.Schema` (Phase 2 remediation)
- 29.1% conventional-commits compliance (Ryan's commits enforce; upstream history doesn't)

## High-risk paths

CODEOWNERS gates ONLY:
- `.github/` — workflow changes
- `custom_components/power_sync/manifest.json` — version bumps affect all HACS users

Cost-calc accuracy and authentication are enforced via P0 review rules above, not CODEOWNERS.

## Auto-merge

**OFF** for this repo. Real users via TestFlight + Google Play. Manual merge always.
