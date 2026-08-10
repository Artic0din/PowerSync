<!-- release: v2.12.1057 -->

## What's Changed

**Report grid connectivity honestly when status is unavailable**

PowerSync no longer assumes a battery is connected to the grid when its backend does not expose a trustworthy terminal grid state. The Grid Status sensor now remains unknown, grid-based automations wait without inventing a transition, command acknowledgement requires an explicit connected or off-grid state, and Energy Flow hides unknown status while continuing to show confirmed states. This was identified from a Sungrow grid-outage report; no outage is inferred from near-zero grid flow, and battery control behavior is unchanged.

Update available via HACS
