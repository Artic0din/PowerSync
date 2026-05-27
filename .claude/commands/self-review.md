---
description: Pre-PR self-check on Ryan's branches
---

Skip if on `sync/*` branch.

1. `ruff check . --fix` (advisory — informational only)
2. `pytest --cov=custom_components/power_sync -x`
3. `gitleaks detect --source . --no-git`
4. Read diff vs `main`. Flag any:
   - Blocking I/O in async (P0)
   - Missing await (P0)
   - Hardcoded secret (P0)
   - HomeAssistantView without `requires_auth = True` (P0)
   - Service without `vol.Schema` (P0)
   - New broad-except without `# noqa` justification (P1)
   - HTTP call without timeout (P1)
5. Report findings — do not auto-fix.
