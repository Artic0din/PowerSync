<!-- release: v2.12.987 -->

## What's Changed

**Restore the post-cap price for capped custom tariffs**
After a daily import allowance is exhausted, PowerSync now returns price sensors, action plans, and live free-price decisions to the configured post-cap rate. Smart Optimization still stops discretionary battery grid charging before the allowance is exceeded, while future tariff days retain their fresh allowance. This corrects plans such as Ergon Energy Solar Sharer 12F after its 24 kWh free window allowance has been consumed.

Update available via HACS
