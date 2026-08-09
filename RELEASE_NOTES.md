<!-- release: v2.12.1056 -->

## What's Changed

**Keep Tesla Fleet and BLE control attached to the correct vehicle**

PowerSync now supports explicit Fleet VIN-to-ESPHome BLE bridge mappings for multi-vehicle households and applies that identity consistently across discovery, telemetry, charging detection, schedules, mobile controls, and physical commands. Unmapped or ambiguous vehicles stay on Fleet control instead of relying on registry order, preventing one Tesla's BLE bridge from affecting another vehicle.

**Preserve safe single-vehicle BLE discovery and configuration**

Single-vehicle Both mode now keeps an unambiguously auto-detected BLE bridge attached to its Fleet VIN, including command routing. Empty bridge-prefix settings fall back to `tesla_ble`, ambiguous auto-detection remains fail-closed, and invalid VIN mapping entries remain visible in the settings form so they can be corrected without retyping.

Update available via HACS
