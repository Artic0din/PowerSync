<!-- release: v2.12.1300 -->

## What's Changed

**Tesla BLE Solar Surplus rate adjustments continue while charging**
When Tesla BLE confirms that the configured vehicle is already charging through fresh vehicle state plus positive current or power telemetry, Solar Surplus can now send a charging-current adjustment without waiting for a separate asleep sensor to refresh. This fixes rate updates that could remain planned but never reach the Tesla BLE amperage entity while the car was actively charging.

The exception is deliberately limited to rate-only updates and still requires fresh, prefix-scoped vehicle telemetry followed by the existing post-write readback. Starting or stopping charging and changing a charge limit retain the explicit wake confirmation gate.

Update available via HACS
