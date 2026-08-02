<!-- release: v2.12.1005 -->

## What's Changed

**Smart Optimization keeps solar battery provenance across reloads**

When Smart Optimization is enabled or reloaded part-way through the day, it now reconciles its private cost ledger with PowerSync's full-day energy counters. Measured battery charging that could not have come from the grid remains valued as solar energy, so a stale or incomplete optimizer ledger no longer suppresses an otherwise economic Flow Power Happy Hour export plan.

**Grid-charged and unknown energy remain conservative**

PowerSync still uses the measured grid-charging cost when it is known. When full-day counters are missing or invalid, stored energy retains the conservative import-price proxy; total site import is treated as the maximum possible grid contribution before any solar provenance is credited.

Update available via HACS
