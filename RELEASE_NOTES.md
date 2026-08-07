<!-- release: v2.12.1041 -->

## What's Changed

**Restore the true Sungrow export limit after an interrupted Spread Export**

When Home Assistant restarts during an optimizer-owned Sungrow Spread Export, recovery now publishes the restored pre-export state before another control cycle can capture it. The temporary export target can no longer become the next restore baseline and restrict later solar export.

**Sequence startup control and reject failed writes**

Optimizer solves now wait for startup hardware restoration to finish, preventing late startup cleanup from cancelling a newly issued export. Sungrow force and export writes that are not confirmed are reported as failures and retried on a later cycle instead of being recorded as successful.

Monitoring Mode remains write-free, and intentional pre-existing export limits are still restored exactly.

Update available via HACS
