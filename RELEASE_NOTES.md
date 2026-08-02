<!-- release: v2.12.1003 -->

## What's Changed

**Correct Happy Hour export valuation for solar-filled batteries**

Smart Optimization now values measured same-day solar charging proportionally instead of pricing the battery's entire stored energy as unknown whenever a small overnight remainder is present. A battery that starts the day above 0% and then fills from solar can therefore export during an economic Flow Power Happy Hour without the remaining overnight reserve incorrectly applying the median import price to the whole battery.

**Conservative energy-cost safeguards remain**

Grid-charged energy continues to use its measured acquisition cost, and stored energy with unavailable provenance still uses the conservative median import-price proxy. Happy Hour remains an economic optimizer window rather than an unconditional export command, so reserve, efficiency, forecast home use, and recharge opportunities still affect the final plan.

Update available via HACS
