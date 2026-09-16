<!-- release: v2.12.1302 -->

## What's Changed

**Tesla BLE active sessions stay on the paired vehicle loadpoint**
When a Tesla BLE bridge is explicitly paired with a Fleet vehicle, an active Solar Surplus session now follows that pairing in EV status. PowerSync keeps the Fleet vehicle as the single visible loadpoint while retaining the BLE session's owner, charge state, and SOC, instead of showing a duplicate Tesla BLE row.

This is a status-identity correction only. Tesla wake, charge-current commands, acknowledgement, and vehicle readback safety checks are unchanged.

Update available via HACS
