---
description: Final pre-merge steps (manual merge — no auto-merge on this repo)
---

All P0/P1 addressed, CI green locally.

1. Rebase autosquash to fold fixup commits:

   ```bash
   git rebase -i --autosquash main
   ```
2. Push (force-with-lease if rebase required it):

   ```bash
   git push --force-with-lease
   ```
3. Flip PR from draft to ready:

   ```bash
   gh pr ready
   ```
4. **Wait for human merge.** No auto-merge on this repo.
5. Confirm to Ryan:
   - PR number and URL
   - Required checks status
   - Codex review status (P0/P1 clean)
   - Ready for manual merge
