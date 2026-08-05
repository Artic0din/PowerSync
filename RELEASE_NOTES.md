<!-- release: v2.12.1027 -->

## What's Changed

**Learn each solar provider's real forecast accuracy**
PowerSync now learns a separate rolling solar-forecast shortfall allowance for Solcast, Open-Meteo, Volcast, and whichever fallback provider actually supplied the optimiser forecast. The learned allowance is persisted across Home Assistant restarts and gradually replaces the old fixed forecast haircut as enough valid local history becomes available.

**Use more forecast solar without weakening Charge By Time**
Charge By Time and Spread Import now reserve battery headroom using the learned kWh shortfall instead of permanently assuming only 80% of forecast solar plus a 3% battery buffer. New and low-history installations retain the previous conservative behaviour, while established accurate forecasts can leave materially more room for solar. The final configured SOC deadline remains a hard optimiser constraint, and the learned allowance is combined with the live nowcast correction without counting the same forecast risk twice.

**Reject misleading samples and expose learning diagnostics**
Solar learning ignores invalid or stale telemetry, curtailment, off-grid operation, telemetry gaps, and near-full battery periods that could make available generation look artificially low. The Solar Forecast sensor now reports the active source, legacy/blending/learned mode, allowance, confidence, observation coverage, and exclusion counters for troubleshooting.

Update available via HACS
