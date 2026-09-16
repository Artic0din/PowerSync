<!-- release: v2.12.1301 -->

## What's Changed

**Tesla BLE keeps current local charging power when Fleet location is stale**
When a configured Tesla BLE bridge reports a newer local charging or plug state than its paired Fleet location, PowerSync now keeps the current BLE power, SOC, and connection on the single physical vehicle. This prevents an older Fleet `away` update from incorrectly folding an actively charging Tesla back into Home Load or dropping its SOC from the EV status.

Current Fleet `away` evidence still wins over older or unavailable BLE presence, so remote charging remains excluded from the home site. The correction applies the same timestamp-aware identity decision to EV status and Home Load attribution.

Update available via HACS
