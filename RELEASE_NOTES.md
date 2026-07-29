<!-- release: v2.12.965 -->

## What's Changed

**SAJ H2 startup now waits for valid inverter telemetry**

After a Home Assistant restart or update, PowerSync now waits until the SAJ
integration has supplied real battery, grid, solar, and load readings before
restoring battery modes or running Smart Optimization. This prevents temporary
unavailable entities from being interpreted as 0% state of charge and avoids
startup mode commands while the inverter integration is still recovering.

Persisted optimizer cleanup automatically retries once telemetry is usable and
is kept pending until the inverter confirms the restore. SAJ TOU schedule
bitmasks are also changed only when an authoritative read is available, so
user-configured charge and discharge slots are preserved.

Update available via HACS
