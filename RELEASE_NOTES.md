<!-- release: v2.12.964 -->

## What's Changed

**Tesla optimizer exports now preserve the software reserve**

PowerSync now accounts for batteries such as Tesla Powerwall that implement
Force Discharge at full power instead of honoring the optimizer's requested
wattage. Targetless export windows are shortened to the duration that can
safely fit above the active optimizer reserve, and the final partial slot is
restored to self-consumption when even one full-power interval would cross the
floor.

Full-power intervals that safely finish exactly at the reserve remain allowed,
and batteries with controllable export power continue to follow the optimizer's
planned wattage.

Update available via HACS
