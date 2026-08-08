<!-- release: v2.12.1047 -->

## What's Changed

**Keep Tesla Home Load correct when native vehicle charging pauses**

PowerSync now treats an explicit 0 W Wall Connector reading as a real stopped-charging state instead of replacing it with stale Fleet or BLE vehicle power. This prevents `sensor.power_sync_home_load` from incorrectly dropping to zero when a plugged-in Tesla is paused by Tesla's native schedule. Missing or invalid Wall Connector power still uses the existing fallback, and multiple connectors continue to aggregate.

Update available via HACS
