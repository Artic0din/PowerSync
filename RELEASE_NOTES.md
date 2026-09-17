<!-- release: v2.12.1309 -->

## What's Changed

**AlphaESS Modbus PV-meter telemetry**
AlphaESS SMILE and Storion installations that report live PV through the optional CT meter now use that positive reading when the inverter-total PV register is unavailable or zero. The existing non-zero inverter-total source remains preferred, and negative meter values are never presented as solar generation.

**Tesla force-charge retry notification**
When an optimizer-initiated Tesla grid-charge enable is accepted but cannot yet be verified by readback, PowerSync keeps the existing fail-closed cleanup and retry behavior while notifying that it is retrying instead of reporting a terminal failure. Manual force-charge failures retain the terminal notification.

Update available via HACS
