<!-- release: v2.12.981 -->

## What's Changed

**Keep SAJ H2 optimizer IDLE holds active**
PowerSync now waits for the SAJ H2 Modbus integration to finish its queued passive-mode switch before verifying an optimizer IDLE hold. This prevents a valid delayed transition from being cancelled back to Self-Use, and PowerSync now retries a genuinely rejected hold instead of reporting success or attempting an unsupported backup-reserve write.

Update available via HACS
