<!-- release: v2.12.1288 -->

## What's Changed

**SolarEdge native self-consumption reconciliation**
PowerSync can now safely reconcile a SolarEdge Modbus Multi battery after it has returned to native Maximize Self Consumption mode. A fresh, identity-checked upstream readback retires only stale Remote Control state; applicable reserve and grid-charge settings must still match, and reconciliation remains acknowledgement-gated and read-only.

**Clearer SolarEdge reconciliation diagnostics**
The SolarEdge reconciliation service now reports the precise safe rejection reason and affected settings where available, making it easier to correct a mismatch without weakening the control-health guard.

Update available via HACS
