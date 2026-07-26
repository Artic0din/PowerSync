<!-- release: v2.12.935 -->

## What's Changed

**Hold SoC now owns Tesla battery control until expiry**
An active user Hold SoC now pauses optimiser action execution, including IDLE backup-reserve adjustments. This prevents routine optimiser reserve writes from superseding the hold timer and leaving a Powerwall at the temporary hold reserve after the countdown ends.

**Restore failures are visible**
The backup-reserve service also rejects late optimiser writes while a hold is active, covering actions already in flight when the hold starts. If a genuine newer user control supersedes the hold timer, PowerSync now records a warning instead of silently abandoning the restore at debug level.

Update available via HACS
