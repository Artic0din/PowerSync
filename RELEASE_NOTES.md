<!-- release: v2.12.1038 -->

## What's Changed

**Label next-day optimizer actions clearly**

The rolling 24-hour optimizer plan now prefixes actions on the next calendar day with "Tomorrow". Cross-midnight ranges such as `18:05 - 17:30` are now shown as `18:05 - Tomorrow 17:30`, so a future export or charge window cannot be mistaken for an action that should be running now.

**Clarify the next scheduled change**

The plan's Next badge and planned battery windows use the same day-aware labels. This is a display-only clarification; optimizer scheduling, reserve protection, and inverter commands are unchanged.

Update available via HACS
