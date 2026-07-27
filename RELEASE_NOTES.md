<!-- release: v2.12.953 -->

## What's Changed

**Separate Sungrow AC inverter generation is now included in site solar totals**

PowerSync now combines a separately configured Sungrow AC inverter with the hybrid inverter for Solar Power, Daily Solar Energy, mobile energy history, EV Solar Surplus, and Smart Optimization live solar inputs. Daily generation remains monotonic through inverter sleep, integration reloads, and Home Assistant restarts, while stale live power, changed endpoints, inferred site totals, and non-Sungrow inverters are kept isolated to prevent double counting.

Update available via HACS
