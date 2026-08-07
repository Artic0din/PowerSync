<!-- release: v2.12.1042 -->

## What's Changed

**Complete interrupted Sungrow export recovery before resuming control**

After Home Assistant restarts during a Sungrow Spread Export, PowerSync now keeps recovery ownership until normal mode, the original grid export limit, and the captured charge and discharge limits are all restored successfully. A partial inverter write can no longer consume the recovery record and leave the system in Forced mode or on a temporary export setting.

**Publish the verified post-recovery inverter state**

The first successful refresh now reads Sungrow telemetry again after restoration, so Home Assistant receives the restored export limit and Self Consumption mode instead of the pre-recovery snapshot. Failed or partial cleanup remains control-blocked and is retried on the next cycle, including failed force-export rollback.

Monitoring Mode remains write-free, ordinary polling still uses a single Modbus read, and intentional pre-existing export limits remain unchanged.

Update available via HACS
