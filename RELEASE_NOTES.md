<!-- release: v2.12.1050 -->

## What's Changed

**Stop satisfied Charge By Time targets from extending grid charging**

Smart Optimization now retires the active SOC deadline constraint as soon as the battery already meets its Charge By Time target. This prevents a completed target from conflicting with No Idle/self-consumption projection and avoids retaining an earlier grid-charge action when a later command-mode projection becomes infeasible.

Unmet future Charge By Time targets still precharge to the required SOC, genuinely economic cheap-grid charging remains available, and the optimizer continues to retain its last physically projected HiGHS plan if a later projection pass is infeasible.

Update available via HACS
