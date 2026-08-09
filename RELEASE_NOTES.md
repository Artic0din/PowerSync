<!-- release: v2.12.1052 -->

## What's Changed

**Restore CovaU TOU schedule and cost tracking**

PowerSync now passes the live, quota-aware CovaU SolarMax contract into the existing TOU Schedule and energy-cost sensors. The dashboard shows the selected plan's local-time import and export rates, and new energy samples accumulate CovaU import costs and export earnings instead of remaining at zero.

Free-import and premium-export rates remain conditional on measured quota confidence and remaining allowance. Unknown or exhausted settlement continues to show conservative base rates, and stale generic tariff data is not substituted for an unavailable CovaU contract. Historical costs from before this update are not reconstructed.

Update available via HACS
