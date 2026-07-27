<!-- release: v2.12.944 -->

## What's Changed

**Volcast solar forecasts can now drive Smart Optimization**
PowerSync can now use the Volcast Home Assistant integration as a selectable solar forecast provider. Volcast's aggregate multi-string forecast feeds Smart Optimization, solar forecast automations, and EV surplus planning without requiring PowerSync to call the Volcast API directly.

**Five-minute detail with complete 48-hour coverage**
PowerSync uses Volcast's five-minute power forecast where available and its hourly forecast for zero-production periods and the rest of the rolling planning horizon. Incomplete or stale Volcast data is rejected so an available Solcast or Open-Meteo fallback can still be used safely.

Update available via HACS
