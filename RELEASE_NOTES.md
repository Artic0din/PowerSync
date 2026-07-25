<!-- release: v2.12.933 -->

## What's Changed

**Make No Idle take precedence over Charge By Time**
No Idle now replaces every Smart Optimization IDLE hold with self-consumption, including holds that were previously retained to keep a Charge By Time target reachable. The Action Plan will no longer show IDLE solely because Charge By Time is enabled. When forecast home load leaves insufficient charging time or headroom, the battery may miss the Charge By Time target instead of holding energy.

**Keep displayed and executed actions aligned**
Current and next actions, monitoring logs, and battery execution now apply the same precedence rule. A residual schedule generated before the setting change is also treated as self-consumption, so GoodWe, Sigenergy, Tesla, and other supported systems do not enter an idle or conserve hold while No Idle is enabled.

Update available via HACS
