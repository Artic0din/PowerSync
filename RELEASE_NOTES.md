<!-- release: v2.12.957 -->

## What's Changed

**Sigenergy dashboards now keep Tesla charging separate from Home load**

For Sigenergy sites using Tesla Fleet or BLE vehicle telemetry, PowerSync now
removes the separately measured Tesla charging power from the Home load branch.
This prevents the dashboard, load history, and optimizer inputs from counting
the same EV demand as both Home and EV power.

Update available via HACS
