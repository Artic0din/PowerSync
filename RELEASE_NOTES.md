<!-- release: v2.12.1019 -->

## What's Changed

**Retry failed Sungrow optimizer restores**
PowerSync now reports failed Sungrow force-charge and self-consumption writes back to Smart Optimization instead of treating them as successful. When a transient Modbus outage prevents the timed restore from stopping forced charging, the optimizer keeps the prior action eligible for retry rather than leaving the inverter stranded in force-charge mode until a restart.

Update available via HACS
