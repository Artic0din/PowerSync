<!-- release: v2.12.1000 -->

## What's Changed

**Optimizer fallback logs now match the plan that remains active**
When a later command-mode projection cannot be satisfied but an earlier feasible HiGHS plan is retained, PowerSync now reports only that retained-plan outcome instead of also claiming it switched to a self-consumption safety hold. Primary infeasible solves still report the genuine safety fallback. This changes diagnostics only; the selected schedule and battery commands are unchanged.

Update available via HACS
