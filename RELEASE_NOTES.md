<!-- release: v2.12.974 -->

## What's Changed

**Sigenergy optimizer exports now retry after rejected commands**
PowerSync no longer records a Smart Optimization export as active when monitoring mode, the network export safety envelope, a missing Modbus host, or a rejected Sigenergy write prevents the hardware command. The export remains retryable on the next optimizer cycle instead of appearing internally active after no command was sent.

Update available via HACS
