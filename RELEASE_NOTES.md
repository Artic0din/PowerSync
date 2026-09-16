<!-- release: v2.12.1303 -->

## What's Changed

**Tesla BLE-only charging remains attributed to the configured loadpoint**
When Tesla BLE-only is selected, PowerSync now ignores retained Tesla Fleet registry devices while collecting EV status. A current BLE charging reading stays on the Tesla BLE loadpoint and is subtracted from Home Load, rather than being replaced by an unselected Fleet vehicle with a zero-power or away state.

This correction is limited to status and EV/Home Load attribution for BLE-only configurations. Solar Surplus ownership, wake/current command safety, command acknowledgement, and charger readback behavior are unchanged.

Update available via HACS
