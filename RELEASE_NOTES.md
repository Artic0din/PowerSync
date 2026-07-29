<!-- release: v2.12.966 -->

## What's Changed

**Native Home Assistant battery integrations now wait for usable telemetry**

The startup protection introduced for SAJ in v2.12.965 now also covers FoxESS
entity mode, GoodWe entity telemetry and EMS control, ESY Sunhome, SolaX,
Fronius GEN24, Neovolt/Bytewatt, SolarEdge, and Anker Solix Home Assistant
integrations. PowerSync waits for real upstream telemetry and control entities
before Smart Optimization or a restore operation can write to the battery.
Temporary `unknown` or `unavailable` states are no longer treated as zero,
while genuine zero readings remain valid.

**Restart recovery preserves pending battery control safely**

Persisted force-mode cleanup and replay now wait for the selected native
integration to recover, retry failed restores without discarding the saved
state, and re-check the expiry time before replaying a command. If a force
window expires while Home Assistant is still restoring the battery
integration, PowerSync restores normal operation instead of briefly issuing
the expired command.

Direct PowerSync Modbus and API connections are unchanged.

Update available via HACS
