<!-- release: v2.12.949 -->

## What's Changed

**Tesla Self-Powered mode no longer falls back into Savings mode**
When Smart Optimization moves from a Tesla force-charge action to self-consumption, PowerSync now cancels any delayed Powerwall charge-kick mode bounce before applying the new action. This prevents a stale autonomous-mode write from racing the optimiser and returning the Powerwall to Savings/Time-Based Control after the action plan has already switched to self-consumption.

Update available via HACS
