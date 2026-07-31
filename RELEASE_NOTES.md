<!-- release: v2.12.985 -->

## What's Changed

**Preflight Smart Schedule site headroom before Tesla charging**
Smart Schedule now refreshes live site telemetry and calculates a safe initial Tesla charging current before issuing the first start command. When the configured site import limit is already reached, telemetry cannot be refreshed, or the remaining headroom is below the charger's minimum, PowerSync waits instead of briefly starting at the charger's maximum rate.

During battery-priority grid windows, the initial EV rate now uses only the site headroom left after the home battery's charging target, matching the ongoing dynamic controller from the first command.

Update available via HACS
