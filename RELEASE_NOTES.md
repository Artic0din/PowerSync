<!-- release: v2.12.1025 -->

## What's Changed

**Keep Scheduled Charging inside the configured price cap**
Scheduled Charging now resolves the active retail price from the current optimizer slot for providers such as Flow Power when a live coordinator price is unavailable. It waits instead of starting when the current retail price is missing, stale, or malformed, so the configured maximum c/kWh no longer fails open.

**Restore dynamic current control for generic chargers on SolaX**
Generic chargers in Scheduled and other dynamic EV modes now consume healthy SolaX site telemetry and adjust amps as available power changes. PowerSync keeps the current setting when telemetry is incomplete or unhealthy instead of issuing a command from an invalid snapshot.

Update available via HACS
