<!-- release: v2.12.1009 -->

## What's Changed

**ZeroCharge now uses the full calendar-month allowance**

PowerSync now treats the configured ZeroCharge kWh value as a daily average and multiplies it by the number of days in the local calendar month. A site can import more than the daily-average amount during one free window while capacity remains, instead of being stopped at 50 kWh each day.

**All eligible imports share one accurately tracked pool**

Household use and battery charging inside the ZeroCharge window both consume the same month-to-date allowance. The optimizer, live cost settlement, baseline comparison, projections, status data, and discretionary grid-charge gate now use the remaining monthly capacity consistently, with separate pools across month boundaries.

**Month state survives restarts and local midnight**

The remaining allowance persists across daily resets and restarts, resets only when the local calendar month changes, and safely migrates existing saved counters. Configuration labels now describe the value as a daily-average allowance so the setting matches the runtime behavior.

Update available via HACS
