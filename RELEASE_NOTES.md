<!-- release: v2.12.976 -->

## What's Changed

**Keep Fronius IDLE from changing minimum SOC**
Smart Optimization now holds Fronius GEN24 / Reserva batteries during `IDLE` using only the temporary PV-charge and discharge power limits. It no longer also raises the persistent Fronius minimum SOC to the battery's current level, so returning to Auto can resume normal self-consumption. If an earlier release already left minimum SOC elevated, reset it to your intended hardware reserve once after updating.

**Retry rejected IDLE restores**
When an inverter rejects the work-mode restore while leaving `IDLE`, PowerSync now keeps the IDLE action pending and retries on the next cycle instead of treating the transition as complete.

Update available via HACS
