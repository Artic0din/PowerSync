<!-- release: v2.12.995 -->

## What's Changed

**GloBird ZeroCharge allowances now reset correctly in multi-day plans**
When today's 50 kWh ZeroCharge allowance has already been used, PowerSync now keeps tomorrow's fresh allowance available in the rolling optimizer horizon. This preserves valid priority-export plans that depend on recharging during the next day's free-import window instead of incorrectly falling back to self-consumption until midnight.

**Daily limits stay isolated across midnight**
Current-day usage, future-day allowances, and overnight ZeroCharge windows are now grouped by local calendar day. Today's remaining allowance cannot consume tomorrow's quota, and an overnight window receives the same midnight reset as the live tariff counter.

Update available via HACS
