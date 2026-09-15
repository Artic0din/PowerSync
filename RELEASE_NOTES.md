<!-- release: v2.12.1291 -->

## What's Changed

**Fronius negative-export curtailment now accepts a fresh successful snapshot**
On Home Assistant runtimes that do not expose an optional coordinator success timestamp, PowerSync no longer defers Fronius load-following telemetry solely because that timestamp is absent. A successful, telemetry-ready coordinator snapshot now follows the same freshness contract as the normalized live-status path, allowing the existing negative-export curtailment decision to reach the inverter control path. Failed, non-ready, and demonstrably stale snapshots remain rejected.

Update available via HACS
