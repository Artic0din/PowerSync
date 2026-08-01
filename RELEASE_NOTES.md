<!-- release: v2.12.999 -->

## What's Changed

**Correct stored-energy pricing after solar charging**

Smart Optimization now treats battery energy as solar-sourced only when measured same-day solar charging, after battery discharge, can account for the battery's full current stored energy. This prevents the median import-rate fallback from incorrectly blocking eligible export of proven solar energy while keeping overnight carry-over, mixed grid charging, and legacy state conservative.

**Flow Power export decisions remain economic**

Happy Hour export is no longer vetoed by a false grid-acquisition cost for proven solar-only inventory. PowerSync still considers future home consumption, terminal battery value, reserve, efficiency, and recharge opportunities, so this correction does not turn Happy Hour into unconditional export.

Update available via HACS
