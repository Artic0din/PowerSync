<!-- release: v2.12.1007 -->

## What's Changed

**Daily energy totals stay with the correct local day**

PowerSync now preserves the local day and month that accumulated solar, grid, battery, load, and cost totals actually belong to when Home Assistant saves or unloads the integration around midnight. A delayed save can no longer label the previous day's totals as the new day, preventing yesterday's generation from appearing as a spike at 00:00 in the mobile Day view after a restart.

**Restart and year-boundary recovery remain consistent**

Same-day totals still restore normally, while stale prior-day snapshots are rejected before new readings are accumulated. Month ownership now includes the year as well, so month-to-date totals also reset correctly across December and January.

Update available via HACS
