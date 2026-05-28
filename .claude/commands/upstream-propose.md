---
description: Prepare a fork PR for upstream submission to bolagnaise/PowerSync
---

For when a fork PR is ready to propose upstream.

1. Verify PR is small (< 400 lines), single-concern, no fork-specific tooling
2. Check commit history clean — `git rebase -i --autosquash main` if needed
3. Strip references to fork-only files (AGENTS.md, CLAUDE.md, ENGINEERING_CONSTITUTION.md, CODEOWNERS, `.github/workflows/fork-*`)
4. Check upstream PR template:

   ```bash
   gh api repos/bolagnaise/PowerSync/contents/.github/PULL_REQUEST_TEMPLATE.md 2>/dev/null
   ```
5. Open upstream PR:

   ```bash
   gh pr create --repo bolagnaise/PowerSync --base main --head Artic0din:<branch>
   ```
6. Use upstream's voice and conventions
7. Do NOT mention fork CI, audit process, Constitution, or AGENTS.md
8. Log in `docs/audits/upstream-prs.md` with PR link + date + status
