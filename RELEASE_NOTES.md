<!-- release: v2.12.1020 -->

## What's Changed

**Keep Solar Surplus targets aligned with measured EV power**
PowerSync now uses a valid measured charger-power reading when calculating active Solar Surplus headroom, instead of allowing a stale higher amp command to inflate the available surplus and requested charging rate. Commanded power remains the safe fallback when measurement is unavailable, and the existing short Tesla restart grace is preserved while telemetry catches up.

Update available via HACS
