<!-- release: v2.12.1292 -->

## What's Changed

**Tesla BLE charging power remains tied to its verified source**
When Tesla BLE reports live charging power, PowerSync no longer lets an unidentified Wall Connector record replace that value merely because the connector's site-wide refresh arrived later. This prevents a lower, ambiguously attributed connector value from being shown as EV power while the difference is assigned to Home Load. Wall Connector measurements with an explicit vehicle identity retain their existing reconciliation path.

**Price-Level Charging now reports a pending Tesla retry accurately**
After an unconfirmed Tesla physical start, PowerSync continues to use its existing safety cooldown and fresh-readback requirement. Subsequent evaluations during that already-pending retry are now reported as waiting for the retry rather than as a new failed start; no additional start command is sent during the cooldown.

Update available via HACS
