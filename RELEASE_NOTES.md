<!-- release: v2.12.1305 -->

## What's Changed

**Tesla BLE Solar Surplus rate changes no longer wait for an unchanged state update**
When a Tesla BLE vehicle is already drawing power, a fresh measured charging current or power reading now permits a Solar Surplus rate adjustment even if its unchanged `Charging` entity has aged past the wake-confirmation window. A newer explicit stopped, complete, disconnected, or unplugged state still blocks the adjustment, and requested-current readback remains required before the change is confirmed.

Update available via HACS
