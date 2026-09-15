<!-- release: v2.12.1294 -->

## What's Changed

**Tesla BLE Solar Surplus active-session ramping and Home Load attribution**
Solar Surplus now retains a fresh prefix-scoped BLE power reading for its unambiguous BLE-only loadpoint. An already-charging Tesla can therefore include its existing draw when calculating the next safe charge rate instead of repeatedly taking the zero-power start path. The EV observation merge now carries the availability of the selected fresh power sample, so a valid BLE measurement is not incorrectly withheld from Home Load accounting. Stale or unavailable readings remain fail-closed.

Update available via HACS
