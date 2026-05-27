---
description: Pre-PR self-check on Ryan's branches
---

Skip if on `sync/*` branch.

1. **PR size check** — `git diff --stat main...HEAD`. If >400 lines, STOP and split.
2. `ruff check . --fix` (advisory — informational only)
3. `pytest --cov=custom_components/power_sync -x`
4. `gitleaks detect --source . --no-git`
5. Read diff vs `main`. Flag any:
   - Blocking I/O in async (P0)
   - Missing `await` (P0)
   - Hardcoded secret (P0)
   - `HomeAssistantView` without `requires_auth = True` (P0)
   - Service without `vol.Schema` (P0)
   - Storage `from_dict()` without explicit HA-timezone date (P0)
   - LP optimiser change without edge-case test (P0)
   - New broad-except without `# noqa` justification (P1)
   - HTTP call without timeout (P1)
   - User-facing string outside `strings.json` (P1)
   - Mobile API contract change without backwards compat (P1)
6. Report findings — do not auto-fix.
