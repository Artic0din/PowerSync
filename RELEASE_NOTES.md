<!-- release: v2.12.998 -->

## What's Changed

**Reliable SolarEdge force charging**

PowerSync now selects the correct SolarEdge storage controls when similarly named production-limit or AC-policy entities are present. Canonical remote-control entities remain discoverable while SolarEdge temporarily reports them unavailable outside Remote Control mode, and configured wildcard prefixes are normalized consistently.

**Safer handling of delayed Modbus updates**

Select and power-limit writes that raise a transport error now have up to 15 seconds to confirm the requested Home Assistant state before PowerSync rolls back. Force Charge only continues after an AC charge-policy error when the selected storage command explicitly permits grid charging, preventing false success for commands that still require the policy change.

Update available via HACS
