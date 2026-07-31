<!-- release: v2.12.988 -->

## What's Changed

**Sungrow daily grid totals now survive integration reloads**
PowerSync now persists the daily lifetime-counter baselines used by Sungrow systems whose hardware daily import/export registers remain at zero. Reloading the integration during the day no longer resets the displayed daily grid import and export totals to zero.

The fallback also handles delayed lifetime registers, transient Home Assistant storage errors, counter rollbacks, and invalid Modbus sentinel values without replacing valid totals.

Update available via HACS
