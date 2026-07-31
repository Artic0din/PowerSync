<!-- release: v2.12.982 -->

## What's Changed

**Clean up inactive Flow Power entities after changing provider**
Switching from Flow Power to another electricity provider now removes Flow Power-only entities and its pricing device while preserving the shared current-price sensors used by the new provider.

**Recover Sungrow Modbus polling without a Home Assistant restart**
A transient Sungrow startup or polling failure now keeps the coordinator available for retry, blocks optimizer control while telemetry is missing or stale, restores interrupted export-control state after recovery, and closes the retained connection normally on unload.

Update available via HACS
