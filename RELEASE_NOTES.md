<!-- release: v2.12.1031 -->

## What's Changed

**Keep FoxESS solar serving the home when export is already stopped**
FoxESS curtailment now checks fresh, trustworthy grid telemetry before taking remote zero-export control at non-negative import prices. When the site is not materially exporting, PowerSync leaves or returns the inverter to Self Use so solar can keep serving the home and charging the battery instead of paid grid power serving the house.

**Preserve export protection and control safety**
Material export still triggers zero-export control. Missing, stale, or malformed telemetry remains conservative, negative-import behavior and force-mode ownership are unchanged, and active curtailment is safely restored and cleared after export stops.

Update available via HACS
