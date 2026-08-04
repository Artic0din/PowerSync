<!-- release: v2.12.1023 -->

## What's Changed

**Honor capped ZeroHero export value during execution**
PowerSync now includes the active ZeroHero Super Export bonus in the final export-command safety check. This prevents a valid 10c or 15c ZeroHero export plan from being cancelled as a zero-value export when the underlying base feed-in tariff is 0c, while still blocking zero-price export outside the bonus window or after the capped allowance is exhausted.

Update available via HACS
