<!-- release: v2.12.1289 -->

## What's Changed

**SolarEdge native self-consumption confirmation**
When a SolarEdge Modbus Multi battery is already confirmed in native Maximize Self Consumption mode, PowerSync now records the optimizer request as successfully confirmed without sending a redundant inverter command. The behavior still requires fresh, identity-checked readback and keeps all control-health, ownership, and baseline safeguards in place.

**Cleaner optimizer action tracking**
Repeated native self-consumption optimizer cycles now advance their action marker after the verified no-write confirmation, avoiding unnecessary retries while preserving retry behavior whenever confirmation cannot be established.

Update available via HACS
