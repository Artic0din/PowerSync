<!-- release: v2.12.990 -->

## What's Changed

**AGL Battery Rewards export planning now recognizes the evening reward window**
Smart Optimization now treats AGL's 17:00–21:00 Battery Rewards period as an explicit export opportunity. When later grid energy is genuinely cheaper, the optimizer can pair reward-period export with only the reachable replacement energy instead of suppressing the current reward slot because of an older acquisition-cost estimate.

Reserve protection, site limits, battery limits, grid-charge eligibility, and profitability checks remain enforced.

Update available via HACS
