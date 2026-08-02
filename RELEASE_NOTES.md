<!-- release: v2.12.1004 -->

## What's Changed

**Cost Neutral now reconciles required imports from an already-covered day**

Cost Neutral now reopens only the genuinely uncovered amount when the final same-day plan includes grid imports after measured or natural-solar export credits had already reduced the initial target to zero. Existing export credit is consumed first, and the optimizer reports and schedules only the remaining amount needed to return the projected daily cost to zero.

**Zero-cost plans cannot manufacture an export target**

The optimizer first evaluates zero-balance and prior-credit plans without discretionary battery export, then uses a bounded reconciliation pass for imports that are already required by the emitted plan. This prevents arbitrary grid charging from creating its own Cost Neutral export allowance while preserving Charge By Time, natural solar export, reserve floors, site and network limits, Monitoring Mode, and manual-control ownership.

Update available via HACS
