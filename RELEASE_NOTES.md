<!-- release: v2.12.1306 -->

## What's Changed

**Smart Schedule now uses real EPEX prices**
Generic Charger Smart Schedule no longer substitutes a generic time-of-use estimate for EPEX pricing. It plans with the timestamp-aligned retail EPEX schedule and uses the active EPEX slot for its live decision, so a peak-priced interval cannot be treated as a synthetic off-peak window.

**Safe handling when EPEX price coverage is unavailable**
When the optimizer does not have valid current EPEX coverage, Smart Schedule does not create a synthetic grid-price plan or start opportunistic grid charging. Solar decisions remain governed by their existing controls.

Update available via HACS
