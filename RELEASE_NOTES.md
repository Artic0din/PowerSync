<!-- release: v2.12.1296 -->

## What's Changed

**Amber metered-cost entities remain available through temporary startup gaps**
Amber usage cost entities, including `sensor.power_sync_amber_usage_today_cost`, now retain their established identities when Amber site discovery or usage-coordinator startup is temporarily unavailable. They report unavailable data until a fresh coordinator is available rather than disappearing after a reload.

Update available via HACS
