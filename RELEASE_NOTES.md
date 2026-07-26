<!-- release: v2.12.937 -->

## What's Changed

**Solar Surplus now falls through to a plugged-in EV**
When parallel charging is disabled in a multi-EV setup, PowerSync now chooses the highest-priority vehicle that is actually plugged in. An unavailable priority vehicle no longer prevents Solar Surplus from starting a lower-priority connected vehicle and using otherwise-curtailed solar.

**Priority and parallel behavior remain intact**
Active Solar Surplus sessions are still left alone, configured vehicle order still determines priority, and parallel mode continues to start every eligible connected vehicle.

Update available via HACS
