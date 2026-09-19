<!-- release: v2.12.1316 -->

## What's Changed

**Restore missing Generic Charger readings in Solar Surplus**
Existing saved vehicle profiles that explicitly selected Generic Charger could
omit its power entity even when that entity was configured globally. Solar
Surplus could then create a session at 0 A without seeing the charger’s measured
load, preventing the normal current adjustment and stop-delay path from handling
an already-running charger.

Solar Surplus now fills missing switch, current, status and power entities from
the enabled Generic Charger configuration when starting those saved profiles.
Explicit per-vehicle entities keep their values, configuration options take
precedence over setup defaults, and disabled Generic Charger settings are not
inherited. Other charger types retain their existing resolution.

The correction is covered by one- and three-phase regressions for measured load,
minimum-current grace and stop commands, plus stale-telemetry and profile
isolation checks. Existing ownership, freshness and external-control policies
are unchanged. Update through HACS and restart Home Assistant to load the fix.

Update available via HACS
