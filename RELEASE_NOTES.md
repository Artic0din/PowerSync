<!-- release: v2.12.1046 -->

## What's Changed

**Restore Tesla linked rate-plan tariffs after force control**

PowerSync now captures the authoritative tariff schedule from Tesla site information before applying a temporary force-charge or force-discharge tariff. Newer linked rate-plan response shapes are supported, and the saved import and export rates are restored with readback confirmation when force control ends.

Tesla's Fleet API does not provide a way to recreate a retailer's authenticated provider connection. This release restores the tariff rates exposed by Tesla without claiming to relink that external account.

**Report rejected Tesla force actions accurately**

Tesla automation actions now require a confirmed successful service response. A rejected force-charge or force-discharge request no longer produces a false success notification, while non-Tesla battery behavior remains unchanged.

Update available via HACS
