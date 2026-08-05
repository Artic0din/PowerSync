<!-- release: v2.12.1029 -->

## What's Changed

**Keep Cost Neutral export plans from collapsing**
Cost Neutral now allows eligible same-day battery export to cover the remaining daily-cost target even when the conservative stored-energy acquisition estimate is above the feed-in rate. This prevents a valid evening export plan from disappearing as the rolling load forecast changes.

**Preserve every optimizer safety boundary**
The exception applies only while the Cost Neutral target remains uncovered. Optimizer and hardware reserve floors, charging deadlines, export eligibility, and site/network limits continue to constrain the plan, and PowerSync still stops discretionary export once the daily target is covered.

Update available via HACS
