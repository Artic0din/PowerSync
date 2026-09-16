<!-- release: v2.12.1304 -->

## What's Changed

**Tesla BLE Solar Surplus no longer overlaps wake/current attempts**
Tesla BLE updates run every 10 seconds, while a vehicle wake and confirmation
can take longer. PowerSync now serializes Solar Surplus updates for each active
loadpoint, so a later tick cannot issue an overlapping wake or charging-current
attempt while the earlier one is still awaiting confirmation.

Different loadpoints remain independent. A queued update is discarded if its
session is replaced or removed before it obtains the lock; existing telemetry,
readback, and hardware-safety checks remain unchanged.

Update available via HACS
