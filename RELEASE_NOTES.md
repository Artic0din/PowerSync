<!-- release: v2.12.1297 -->

## What's Changed

**SolarEdge Modbus Multi v4 storage control readback is supported**
PowerSync now recognizes SolarEdge Modbus Multi v4's persistent storage-control
component after a successful coordinator refresh. Reconciliation, Self
Consumption, and Force Discharge no longer reject a valid v4 storage read as
stale solely because v4 does not replace the v3 decoded dictionary.

The existing safety gates remain in place: PowerSync still requires a newer
successful upstream update and confirmed storage-control availability, retains
v3's replacement-identity check, and fails closed for stale, failed, malformed,
or unavailable storage data before issuing a storage command.

Update available via HACS
