<!-- release: v2.12.1298 -->

## What's Changed

**Tesla Fleet completed-charge detection**
PowerSync now resolves a Tesla Fleet vehicle's charging-state entity through its exact VIN-linked Home Assistant device before deciding to start Price-Level or Solar Surplus charging. A vehicle reporting `Complete` at its configured charge limit is no longer repeatedly sent an automated start command solely because its entity name differs from the VIN.

**Clearer Powerwall grid-charge compatibility status**
When Tesla accepts a Force Charge grid-enable command but its valid `site_info` readback omits the grid-charging field, PowerSync retains the bounded compatibility handling without also logging that result as a failed verification. Opposite, invalid, rejected, and transport-failed readbacks remain unconfirmed and fail closed.

Update available via HACS
