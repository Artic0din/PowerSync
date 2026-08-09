<!-- release: v2.12.1053 -->

## What's Changed

**Keep sleeping configured Tesla vehicles visible**

PowerSync now keeps configured Fleet-only Tesla profiles in loadpoint status while sleeping telemetry cannot confirm cable state or battery level. This prevents a real configured vehicle from disappearing when its Fleet data goes idle, while BLE telemetry continues to merge into its paired vehicle and unconfigured bridge rows remain hidden.

Update available via HACS
