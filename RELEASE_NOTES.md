<!-- release: v2.12.1293 -->

## What's Changed

**Tesla BLE Solar Surplus starts for legacy single-loadpoint setups**
Tesla BLE installations that have Solar Surplus enabled but do not yet have a saved per-vehicle charging profile can now use their single configured BLE loadpoint. Existing eligibility, location, plug-state, ownership, start-delay, and command/readback safeguards remain in place. Multiple BLE loadpoints still require an explicit vehicle profile, so PowerSync does not guess which vehicle to control.

Update available via HACS
