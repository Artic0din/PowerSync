<!-- release: v2.12.973 -->

## What's Changed

**Teslemetry Energy Site updates now stream live**
PowerSync now listens to Teslemetry's Energy Site `live_status` stream, giving Tesla Powerwall telemetry faster updates while reducing repeated API polling. The same telemetry mapping continues to feed PowerSync's sensors and energy accounting, while Tesla Fleet API and PowerSync Cloud connections remain on their existing paths.

**Automatic polling fallback keeps telemetry resilient**
If the stream disconnects, goes silent, replays stale data, or returns an empty snapshot, PowerSync automatically falls back to its existing Teslemetry REST and paired local Powerwall paths. Stream tasks reconnect with bounded backoff and shut down cleanly during Home Assistant reloads.

Update available via HACS
