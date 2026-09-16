<!-- release: v2.12.1299 -->

## What's Changed

**Tesla Solar Surplus skips vehicles already at their charge limit**
Solar Surplus now checks Tesla Fleet completion before creating a managed session. A vehicle already reporting `Complete` is skipped in favour of the next eligible vehicle, preventing command-neutral session, notification, and history churn while retaining the existing runtime check for a car that becomes complete during an active session.

**EV Energy Flow uses one loadpoint consistently**
When more than one EV loadpoint is active, the vehicle-labelled EV sensor now reports the selected loadpoint's own power alongside its name and state of charge. The site-wide EV aggregate remains available in the canonical status snapshot for aggregate consumers, avoiding a total being shown as the power for one vehicle.

Update available via HACS
