<!-- release: v2.12.941 -->

## What's Changed

**No Idle now clears stale Charge By Time holds**
When No Idle is enabled while an older Charge By Time IDLE slot is still cached, PowerSync now exits that hold to self-consumption instead of deduplicating it as already applied. GoodWe systems restore from `conserve` to Auto / General without changing the configured DOD, and a failed restore remains pending for retry. Waiting-for-data and expired-plan status also show self-consumption instead of IDLE while No Idle is enabled.

Update available via HACS
