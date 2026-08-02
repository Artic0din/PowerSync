<!-- release: v2.12.1002 -->

## What's Changed

**Grid-charge SOC caps now stop optimizer charging at the configured limit**
PowerSync now clips forced grid-charge commands to the remaining cap headroom and returns the battery to self-consumption once live SOC reaches the configured limit, including during free, negative-price, and tariff-credit slots. An already-active optimizer charge is also stopped at the cap instead of being prolonged by its minimum commitment window.

Natural solar charging can still continue above the grid-charge cap, 100% caps retain the previous free-slot behavior, and user-started force charging remains under user control. When a lower cap is configured but live SOC cannot be verified, optimizer-owned grid charging now waits safely for valid telemetry.

Update available via HACS
