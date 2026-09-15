<!-- release: v2.12.1290 -->

## What's Changed

**Reliable Tesla BLE manual starts with a configured display label**
Tesla BLE manual charging now uses the single safely discovered ESPHome entity prefix when a Fleet VIN is selected. This prevents an older display-style prefix such as `Tesla BLE` from being treated as an entity ID and failing before the BLE wake/start command. Multi-bridge and ambiguous vehicle setups remain fail-closed.

Update available via HACS
