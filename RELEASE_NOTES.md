<!-- release: v2.12.1295 -->

## What's Changed

**Tesla EV power and Home Load retain a usable measurement**
When Tesla Fleet telemetry supplies a fresh charging-power reading and a newer
BLE status update has no power measurement, PowerSync now keeps the usable
power value. This prevents the EV flow and normalized Home Load from becoming
unknown solely because the status update lacks a measurement.

**Sigenergy export preserves active solar generation**
Optimizer export commands now select PV-first discharge whenever fresh solar is
active. The configured PCC export ceiling remains a safety limit and no longer
causes ESS-first mode to suppress PV and turn an export action into grid import.

Update available via HACS
