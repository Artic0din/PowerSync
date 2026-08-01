<!-- release: v2.12.997 -->

## What's Changed

**Cost Neutral settlement accuracy**

Cost Neutral now prices same-day grid imports caused by its own export plan inside the optimization constraint. If the battery cannot fully neutralize the day after those imports, status reports the remaining amount instead of treating the original export target as covered.

**Capped tariff credits**

The final schedule guard now allocates limited export bonus quota chronologically across natural solar and battery export, preventing the same capped credit from being counted more than once.

Update available via HACS
