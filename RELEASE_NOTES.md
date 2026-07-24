<!-- release: v2.12.929 -->

## What's Changed

**Keep spread exports inside the matching price window**
Spread Export Across Window now flattens planned battery discharge only across consecutive slots with the same export price. Multi-rate plans such as AGL Battery Rewards no longer dilute a high-value reward-window export into an adjacent low-value shoulder period.

**Safe forecast fallback**
Missing, incomplete, or non-finite export-price forecasts retain the previous contiguous-window behaviour instead of interrupting schedule generation.

Update available via HACS
