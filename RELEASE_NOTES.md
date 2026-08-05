<!-- release: v2.12.1028 -->

## What's Changed

**Keep Solar Only inside charger and home-battery limits**
PowerSync now reads a generic charger's live Home Assistant current range before dynamic control, so Beny and other chargers with a 6 A floor are no longer sent invalid 5 A requests. Solar Only also remains paused until the configured home-battery start SOC instead of resuming at the lower pause threshold.

**Apply generic charger limits across dynamic modes**
Solar Only, Limited Grid + Solar, battery-target charging, and full-battery curtailment now use the charger's authoritative minimum and maximum current. Invalid or contradictory entity ranges fail safely, while explicit zero-amp stop commands remain available.

Update available via HACS
