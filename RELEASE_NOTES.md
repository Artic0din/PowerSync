<!-- release: v2.12.1043 -->

## What's Changed

**Upload saved AGL and custom TOU schedules to Tesla on manual sync**

The `power_sync.sync_tou_schedule` service now sends the saved static tariff to Tesla Powerwall sites instead of passing an empty forecast into the dynamic-price converter. AGL Battery Rewards periods, every configured season, and both import and export rates are preserved, while the PowerSync schedule used by the dashboard and optimizer remains unchanged.

**Validate the complete Tesla tariff before upload**

Static tariffs now use a clean Tesla payload and exact multi-season readback confirmation. Missing, malformed, inconsistent, or non-finite tariff data fails safely without replacing the Powerwall's current schedule.

This restores the documented manual sync behavior only. Automatic AGL tariff uploads remain disabled, and Monitoring Mode plus active force-session protections are unchanged.

Update available via HACS
