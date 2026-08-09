## What's Changed

**Keep Tesla BLE bridges attached to the correct Fleet vehicle**

PowerSync now maps multi-vehicle Tesla BLE bridges to explicit Fleet VINs instead of relying on discovery order.
Ambiguous vehicles safely use Fleet control, preventing another bridge's telemetry or commands from being applied to them.
