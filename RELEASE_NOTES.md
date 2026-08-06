<!-- release: v2.12.1033 -->

## What's Changed

**Keep priority exports stable across rolling optimizer solves**
PowerSync now honours an active 20-minute export commitment when a fresh five-minute solve temporarily switches to self-consumption during the same eligible priority window. This prevents a newly restarted Flow Power Happy Hour or other priority export from being cancelled again at the very next solve because forecasts moved marginally.

**Preserve export safety and window boundaries**
The commitment releases immediately when the tariff window or export eligibility closes, the export price falls below the execution threshold, EV preservation, calibration, or a demand guard blocks discharge, or the remaining commitment would cross the configured battery reserve. Outside the commitment, the optimizer continues to reassess export economics normally.

Update available via HACS
