<!-- release: v2.12.954 -->

## What's Changed

**Smart Schedule now restarts Tesla charging when site headroom returns**

When PowerSync pauses a Tesla at 0A to preserve the configured home-battery charge target and site import limit, it now reapplies the requested current and sends a new start command to the exact BLE or Fleet vehicle once at least the minimum charging power is available again. Failed starts are retried on the next control cycle, another Tesla's site-wide power reading cannot suppress the restart, and a manual takeover still blocks stale automation commands.

Update available via HACS
