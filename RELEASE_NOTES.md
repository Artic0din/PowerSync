<!-- release: v2.12.943 -->

## What's Changed

**Sigenergy Energy Summary costs now use the dynamic tariff**
Week, Month, and Year import cost and export earnings now use the same Sigenergy half-hour tariff schedule and calendar energy rows shown in the mobile app. This prevents daily cost-sensor resets from producing implausibly small longer-period totals, including with Flow Power pricing.

**Calendar cost estimates preserve daily and half-hour timing**
PowerSync now distinguishes daily history from hourly history by the requested period and row timestamps, and hourly estimates average both half-hour prices. Month and Year rows are no longer mistaken for hourly data, and the final 23:30 tariff slot is included correctly.

Update available via HACS
