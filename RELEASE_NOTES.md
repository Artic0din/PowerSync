<!-- release: v2.12.972 -->

## What's Changed

**SAJ H2 force modes now wait for queued inverter mode changes**
PowerSync now gives the SAJ H2 Modbus integration time to finish its queued Time-of-Use mode change before deciding that force charge or force discharge failed. This prevents a valid export command from being immediately cancelled and restored to Self-Use simply because the AppMode sensor had not updated within the first half-second, while retaining a bounded timeout and safe restore when the inverter genuinely does not confirm the requested mode.

Update available via HACS
