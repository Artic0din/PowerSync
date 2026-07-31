<!-- release: v2.12.989 -->

## What's Changed

**Sungrow iHomeManager telemetry-only safety is now enforced across every control path**
PowerSync now rejects an AC-inverter curtailment configuration that points at the same Modbus endpoint as the active Sungrow battery or iHomeManager telemetry connection. Existing same-endpoint configurations are also blocked before manual curtail or restore can create a controller, closing a path that could otherwise bypass the telemetry-only write guard.

The provider API now consistently reports and preserves Monitoring Mode as enabled for iHomeManager connections. A genuinely separate AC-coupled inverter endpoint remains supported and controllable.

Update available via HACS
