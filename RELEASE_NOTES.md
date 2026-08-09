<!-- release: v2.12.1054 -->

## What's Changed

**Keep multi-Tesla smart charging on the correct vehicle**

PowerSync now keeps Fleet VINs and their paired Tesla BLE bridges isolated across charging detection, automatic starts, and stop decisions. One vehicle's BLE bridge can no longer make another Tesla appear to be charging, ambiguous multi-vehicle starts fail safely instead of selecting the first car, and every paired BLE alias is deduplicated without hiding genuinely separate BLE vehicles.

**Protect charging ownership and reload state**

Auto Schedule stops now respect the active loadpoint owner, time-window expiry targets only the affected EV, and integration reloads invalidate entry-owned EV timers and callbacks without sending a charger command. This prevents stale schedule state from interrupting a manual or newer charging session.

Update available via HACS
