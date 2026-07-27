<!-- release: v2.12.946 -->

## What's Changed

**Tesla BLE vehicles no longer disappear when ESPHome node status is omitted**
PowerSync now recognizes the Tesla BLE component's `BLE Status` entity when a bridge does not expose the optional generic ESPHome `Status` entity. Configured single- and multi-vehicle BLE setups remain visible after Sync, including while a car is disconnected or asleep.

Update available via HACS
