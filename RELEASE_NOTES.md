<!-- release: v2.12.980 -->

## What's Changed

**Keep Import kWh totals accurate through telemetry changes**
The cumulative **Import kWh** automation trigger now reconciles daily meter readings with its live grid-power fallback without counting the same energy twice. Temporary counter gaps, partial recovery, same-day rollbacks, restarts, and overnight counter resets no longer create false import spikes or discard recoverable measured energy, so the configured action still runs once at the correct window threshold.

**Enable Import kWh across supported battery telemetry**
Automation state discovery now includes ESY Sunhome, SolarEdge, Anker Solix, and custom external-controller coordinators, allowing their whole-site import telemetry to drive the same cumulative trigger.

Update available via HACS
