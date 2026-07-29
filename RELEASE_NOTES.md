<!-- release: v2.12.971 -->

## What's Changed

**EV mode takeovers no longer apply a stale Solar Surplus adjustment**
When Scheduled Charging or another EV mode takes control of a charger while a Solar Surplus update is already running, PowerSync now discards the superseded update before it can change the charging current. The active mode keeps sole control of the loadpoint, while normal Solar Surplus timer cancellation and same-mode handovers continue unchanged.

Update available via HACS
