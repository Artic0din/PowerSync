<!-- release: v2.12.1037 -->

## What's Changed

**Correct Sunday-only time-of-use matching**

PowerSync now preserves Sunday as day 0 instead of treating a Sunday-only tariff period as an all-week period. This prevents weekend off-peak and AGL Battery Rewards periods from overriding the correct weekday peak import rate.

**Keep displayed and planned prices aligned**

The current electricity price, LP price forecast, EV price evaluation, and Sigenergy tariff sync now select the same weekday period as the TOU schedule. A Friday peak rate such as 42.42c/kWh will no longer be replaced by a Sunday off-peak rate such as 32.12c/kWh.

Update available via HACS
