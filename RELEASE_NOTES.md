<!-- release: v2.12.968 -->

## What's Changed

**Tesla Hold SoC now finishes restoring when optimizer control overlaps**
Stop & Resume Auto now clears an expired Tesla Hold SoC after the battery mode, grid-charging policy, and backup reserve have been restored successfully, even when Smart Optimization also has an active or saved force action. The completed hold no longer remains stuck at `0:00` or reappears after an integration reload.

**Smart Optimization toggles no longer time out while the battery restores**
The mobile settings API now acknowledges Smart Optimization enable and disable requests immediately while any slower tariff, inverter, or battery cleanup continues in the background. Rapid toggle requests are applied in order, and ordinary optimizer setting changes remain synchronous.

Update available via HACS
