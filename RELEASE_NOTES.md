<!-- release: v2.12.1317 -->

## What's Changed

**Solar Surplus shows unsuccessful and unconfirmed stops accurately**
When the stop delay had elapsed but a stop failed or could not be confirmed,
the EV panel could keep showing an expired countdown. The loadpoint now shows
the pending, unsuccessful or unconfirmed stop and retry state. Its requested
zero-current target remains separate from the previous commanded current and
fresh measured current/power. A later independent stopped observation can clear
the warning without claiming that PowerSync caused the stop.

This corrects status reporting; it does not bypass Tesla BLE wake confirmation
or establish that a previously failing vehicle will now stop. Existing wake,
readback, ownership and retry safeguards remain in place.

**Scheduled Charging acquires and cleans up each Tesla's own session**
Scheduled Charging now reconciles each eligible physical vehicle, including a
Tesla already charging under Solar Surplus. A rejected start no longer marks
Scheduled active, and partial starts retain per-vehicle outcomes so the remaining
eligible vehicle can be retried. Paired Fleet/BLE identities are handled once;
Manual, external and Smart Schedule ownership remain protected.

Window-end and disable cleanup target Scheduled-owned vehicles even while
Price-Level charging continues on another car. Temporary discovery/eligibility
loss no longer hides an acquired session from cleanup. Ownership is rechecked
after asynchronous work so an intervening takeover is respected. An unsuccessful
stop that has already released its controller remains unconfirmed rather than
being reported as successful on the next cycle; it is not resent as an unowned
hardware command. Home-battery preservation remains coherent with accepted
sessions and pending stop outcomes.

The corrections include regressions for failed and unconfirmed stops, fresh
versus stale or other-vehicle readback, partial Scheduled acquisition, multiple
vehicles, inferred/explicit BLE pairing, ownership changes during awaits and
window cleanup. Update through HACS and restart Home Assistant. Publication and
simulated regression checks do not establish installation or live charging.

Update available via HACS
