---
description: Pull from bolagnaise/PowerSync upstream, open advisory-CI PR
---

1. Verify upstream remote exists:

   ```bash
   git remote -v | grep -q bolagnaise || \
     git remote add upstream https://github.com/bolagnaise/PowerSync.git
   ```
2. Fetch upstream:

   ```bash
   git fetch upstream main
   ```
3. Create sync branch (advisory CI only):

   ```bash
   git checkout -b sync/upstream-$(date +%Y%m%d)
   git merge upstream/main
   ```
4. Resolve conflicts if any. Should be rare — fork shouldn't diverge much.
5. Push and open PR:

   ```bash
   git push origin HEAD
   gh pr create --base main \
     --title "sync: upstream $(date +%Y-%m-%d)" \
     --body "Routine upstream sync from bolagnaise/PowerSync."
   ```
6. Advisory CI runs (does not block). Pass = merge. Fail = investigate but typically still merge — upstream is source of truth.
7. After merge to `main`, rebase any open Ryan feature branches:

   ```bash
   git checkout <feature-branch>
   git rebase main
   ```
