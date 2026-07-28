<!-- release: v2.12.958 -->

## What's Changed

**Smart Schedule Solar Surplus sessions no longer stop on their own EV load**

When Smart Schedule is already charging an EV from solar surplus, PowerSync now
leaves low-surplus ramp-down and stop decisions to the active Solar Surplus
controller. This prevents multi-EV sites from repeatedly starting and stopping
a BLE Tesla when the site load includes the EV's own charging power, while
preserving the configured minimum-surplus gate for new starts.

Update available via HACS
