---
description: Prepare a fork PR for upstream submission
---

For when a fork PR is ready to propose to `bolagnaise/PowerSync`.

1. Verify PR is small (< 400 lines), single-concern, no fork-specific tooling
2. Check commit history clean — squash fixups if needed
3. Verify no references to fork-only AGENTS.md, CODEOWNERS, or process
4. Check upstream PR template at `bolagnaise/PowerSync/.github/PULL_REQUEST_TEMPLATE.md`
5. Open PR via: `gh pr create --repo bolagnaise/PowerSync --base main --head Artic0din:<branch>`
6. Use upstream's voice and conventions
7. Do NOT mention fork CI or audit process
8. Do NOT reference Constitution or fork AGENTS.md
9. Update `docs/audits/upstream-prs.md` with PR link + date
