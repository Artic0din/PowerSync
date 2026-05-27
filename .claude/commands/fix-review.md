---
description: Address Codex P0/P1 findings on Ryan's PRs
---

Skip if PR branch starts with `sync/`.

1. `gh pr view --comments`
2. Triage by P0/P1/P2/P3
3. Apply minimal fix per P0/P1 as fixup commit
4. Verify: `ruff check . && pytest -x`
5. Reply per thread: `Fixed in <sha>. <rationale>`
6. P2/P3: `Acknowledged — tracked in audit Phase N.`
7. Cap at 3 rounds. After round 3, surface.

Stop conditions — bail to Ryan:
- Same finding reappears after 2 rounds
- Coverage drops
- Test count drops
- PR grows past 500 lines during fixes
