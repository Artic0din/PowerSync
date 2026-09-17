<!-- release: v2.12.1308 -->

## What's Changed

**Tesla BLE stops now require fresh vehicle confirmation**
PowerSync no longer treats a completed BLE switch service as proof that Tesla charging stopped. It waits for a newly observed stopped, complete, disconnected, or not-charging state; an unconfirmed command remains explicitly unconfirmed, and stale per-vehicle charging telemetry cannot create a replacement observed session after a stop or reload.

**Sigenergy optimizer charging preserves failed-write safety**
When a Sigenergy force-charge write is not confirmed, PowerSync now reports that failure back to the optimizer instead of advancing the effective charge action. This matches the existing fail-closed force-discharge behavior and allows the normal retry path to handle the failed command.

Update available via HACS
