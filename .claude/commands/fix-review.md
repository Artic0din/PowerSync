---
description: Address Codex P0/P1 findings on Ryan's PRs
---

Skip if PR branch starts with `sync/`.

1. `gh pr view --comments`
2. Triage by P0/P1/P2/P3
3. Apply minimal fix per P0/P1 as fixup commit:

   ```
   fix({scope}): {one-line description}

   Resolves Codex {P0|P1} finding.
   ```
4. Verify: `ruff check . && pytest -x`
5. Reply per thread: `Fixed in <sha>. <rationale>`
6. P2/P3: `Acknowledged — tracked in audit Phase N.`
7. Cap at 3 rounds. After round 3, surface to Ryan.

Stop conditions — bail to Ryan:
- Same finding reappears after 2 rounds
- Codex flags P0 in `optimization/coordinator.py` LP solver core
- Codex flags P0 in `views/` auth/authz
- Codex flags P0 in battery vendor write paths
- Coverage drops
- Test count drops
- PR grows past 500 lines during fixes
