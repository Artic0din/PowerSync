<!-- release: v2.12.978 -->

## What's Changed

**Add safe Sungrow iHomeManager telemetry**
Sungrow SH users can now select an iHomeManager / WiNet-S forwarding connection on port 503 or 504. This connection is explicitly telemetry-only: PowerSync keeps Monitoring Mode enabled, blocks every native inverter write, and restores normal operation before changing an existing direct-control setup to iHomeManager.

**Use Belgium's native 15-minute EPEX prices**
The Belgian EPEX bidding zone now preserves the API's quarter-hour boundaries through price sensors and Smart Optimization. Other EPEX regions continue to use their published hourly intervals.

**Trigger automations from cumulative grid import**
PowerSync Automations can now fire once when measured grid import reaches a configured kWh threshold inside a required daily time window. The trigger persists its window state across restarts, resets on the next local day, and can use live power integration when a cumulative daily meter is unavailable.

**Support capped custom-tariff import allowances**
Custom tariffs can now define a daily discounted or free import allowance with a time window and kWh cap. Smart Optimization tracks the measured allowance, applies its marginal price only while energy remains, persists settlement state, and can stop discretionary battery grid charging before the cap is exhausted. An editable Ergon Energy Solar Sharer 12F 2026 template is included with its 24 kWh 11am-2pm allowance; review the rates and set your applicable feed-in tariff before saving.

Update available via HACS
