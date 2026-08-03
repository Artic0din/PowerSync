<!-- release: v2.12.1011 -->

## What's Changed

**Solar Surplus charging now recovers from a stopped Tesla session**

When Tesla reports that charging has stopped and a reliable power sensor reads 0 W, PowerSync now clears the stale commanded load from its surplus calculation. After the configured sustained-surplus delay, it sends a physical start command and applies the recalculated current automatically, without requiring Solar Surplus to be disabled and re-enabled.

**Telemetry fallbacks and restart safeguards remain conservative**

PowerSync keeps the commanded-load fallback when charging state or power telemetry is missing, unavailable, or invalid. A short post-start grace period prevents repeated commands while Tesla telemetry catches up, and a stopped partial charge is no longer mistaken for a completed charge.

Update available via HACS
