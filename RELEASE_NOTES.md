<!-- release: v2.12.984 -->

## What's Changed

**Coordinate EV and home-battery charging during free electricity**
Smart Schedule now reads nested Tesla-style TOU periods used by GloBird tariffs, treats tariff rounding values up to 0.001 c/kWh as free, and schedules feasible departure charging inside those free windows instead of deferring it to paid deadline hours.

During a free window, the home battery's maximum-charge target remains in control and the EV receives the remaining site headroom. If the free window is no longer sufficient, paid deadline recovery still uses the EV's maximum available rate.

**Reconcile externally started EV charging at the battery floor**
When an external vehicle or charger schedule starts a session, Smart Schedule now detects and stops that untracked session if the configured home-battery consume floor is reached.

Update available via HACS
