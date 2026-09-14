<!-- release: v2.12.1287 -->

## What's Changed

**Tesla BLE stopped charging status reconciliation**
PowerSync now gives a newer Tesla BLE `Stopped`, `Complete`, or disconnected state precedence over an older positive charge-power value that was merely re-reported. This prevents a stopped vehicle from remaining displayed and accounted for as charging while preserving an honest unknown-power boundary rather than claiming physical zero-current proof.

**Tesla BLE delayed-stop session protection**
Delayed positive telemetry immediately after a successful PowerSync stop no longer starts a replacement observed charging session during the stop-settling window. Fresh newer charging evidence can still establish a genuine later external restart.

Update available via HACS
