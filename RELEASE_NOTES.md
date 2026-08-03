<!-- release: v2.12.1013 -->

## What's Changed

**Tesla force discharge now reports failed tariff uploads correctly**
PowerSync now requires a confirmed service result before the optimiser treats a Tesla force-discharge request as successfully applied. If local Powerwall mode controls succeed but the Fleet API tariff upload fails, the optimiser no longer reports the export command as successful and will retry on a later cycle. Accepted tariffs that are still propagating through Tesla's cloud retain the existing bounded retry and restore protection.

Update available via HACS
