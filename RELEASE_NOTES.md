<!-- release: v2.12.940 -->

## What's Changed

**BLE Tesla automations now stop the vehicle that triggered them**
EV charging automations now carry the detected ESPHome Tesla BLE vehicle through to the command path. In mixed Fleet/Teslemetry and BLE setups, a stop action no longer checks an unrelated stopped Tesla and reports success without sending a command; it targets the charging BLE vehicle and issues the stop through its own charger switch.

Update available via HACS
