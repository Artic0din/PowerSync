<!-- release: v2.12.1314 -->

## What's Changed

**Smart Schedule can recover a stopped BLE Tesla without a charging-power sensor**
BLE-only vehicles can report that they are stopped at 0 A while exposing no
charging-power sensor. PowerSync could miss their charging-state entity and
retain an old commanded rate, sending rate adjustments without restarting the
car. Solar Surplus and grid Smart Schedule recovery now recognize the exact
vehicle's BLE state and measured current, including both supported entity aliases.

**Recovery continues to require current evidence and the normal charging gates**
Stopped-session recovery rejects stale or future samples, another vehicle's
readings and writable current limits. Solar recovery still requires sufficient
sustained surplus and respects optimizer zero-power slots and post-start grace.
Grid recovery retains its existing eligibility, ownership and bounded retry
checks. Physical start confirmation remains required where previously enforced.

**Clearer BLE start-failure diagnostics**
BLE start errors now include the exception class, so errors with an empty message
are no longer recorded as an unexplained blank failure.

This fixes a reproduced recovery defect; it does not establish the cause of
every BLE transport failure. This release does not change battery optimization
or add a second surplus target above Smart Schedule's target SOC.

Update available via HACS
