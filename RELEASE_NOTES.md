<!-- release: v2.12.1051 -->

## What's Changed

**Keep Tesla BLE bridges attached to the correct vehicle**

PowerSync now treats configured BLE devices as command and telemetry bridges for their paired Fleet vehicle rather than separate loadpoints. BLE remains the preferred free command path when a matching bridge is available, while vehicles without a paired bridge safely fall back to Fleet instead of controlling another car. A second ESP32 is supported by matching configured BLE prefixes to deduplicated Fleet vehicles in order.

**Honor per-vehicle Smart Schedule charging limits**

Smart Schedule now resolves legacy vehicle selections against deduplicated Tesla identities and uses each vehicle's configured capacity, minimum and maximum current, voltage, and phase count when calculating required energy, duration, and command limits. A 10 A single-phase Tesla will no longer be planned at a higher default rate, while uncapped and multi-phase vehicles retain their established defaults.

Update available via HACS
