# PowerSync — Exhaustive Python Audit Data
Generated: 2026-05-26
Scope: `custom_components/power_sync/` (88 files) + `tests/` (63 files)

---

## CAT1: `except Exception:` / `except:` — 178 total (102 broad, 76 silent)

36 files affected. Format: `file: N broad / M silent`

| File | Broad | Silent | Silent line numbers |
|---|---|---|---|
| `__init__.py` | 260 | 82 | 2238,2276,2490,2618,2716,2741,2942,3870,4872,4941,5134,5516,5554,5576,6339,9572,9587,11572,11584,11608,13133,13256,13321,13765,13823,13967,13990,14040,14957,16332,16572,16653,16798,16844,16858,17352,17475,18283,18347,18374,19011,19070,19074,19120,19135,21802,23459,23558,23599,23635,23671,23707,23746,23781,23816,23851,23887,24067,24132,24199,24374,25047,25262,25544,26035,26187,26329,26402,26747,26759,27072,27299,27497,27568,27571,28075,28764,28773,28781,28793,28801,28844 |
| `aemo_api.py` | 2 | 0 | — |
| `alphaess_api.py` | 1 | 0 | — |
| `auto_update.py` | 4 | 2 | 136,289 |
| `automations/__init__.py` | 9 | 2 | 415,456 |
| `automations/actions.py` | 76 | 22 | 422,1084,1090,2639,2647,2724,3806,4238,4762,4767,4805,4997,5044,5193,5226,5241,5636,6062,6100,6139,6219,6247 |
| `automations/ev_charging_planner.py` | 43 | 12 | 701,1197,2849,4152,5242,5409,5423,5925,5973,6006,6200,6229 |
| `automations/ev_charging_session.py` | 2 | 0 | — |
| `automations/ev_pricing.py` | 1 | 0 | — |
| `automations/weather.py` | 1 | 0 | — |
| `config_flow.py` | 35 | 5 | 2517,2621,2757,4743,6297 |
| `coordinator.py` | 49 | 18 | 491,1891,1904,1959,2063,2080,2110,2123,2544,2565,3607,3958,6952,6978,7019,7134,7137,7655 |
| `epex_api.py` | 1 | 0 | — |
| `flow_power_portal.py` | 4 | 0 | — |
| `foxess_api.py` | 1 | 0 | — |
| `inverters/alphaess.py` | 12 | 0 | — |
| `inverters/base.py` | 1 | 0 | — |
| `inverters/enphase.py` | 17 | 3 | 863,963,985 |
| `inverters/foxess.py` | 2 | 1 | 449 |
| `inverters/fronius.py` | 8 | 0 | — |
| `inverters/fronius_reserva.py` | 3 | 0 | — |
| `inverters/goodwe.py` | 8 | 0 | — |
| `inverters/goodwe_battery.py` | 5 | 1 | 117 |
| `inverters/huawei.py` | 9 | 0 | — |
| `inverters/neovolt.py` | 6 | 0 | — |
| `inverters/saj_h2.py` | 3 | 0 | — |
| `inverters/sigenergy.py` | 24 | 0 | — |
| `inverters/solaredge.py` | 4 | 0 | — |
| `inverters/solax.py` | 5 | 0 | — |
| `inverters/solax_battery.py` | 2 | 1 | 1031 |
| `inverters/sungrow.py` | 8 | 0 | — |
| `inverters/sungrow_sh.py` | 22 | 0 | — |
| `inverters/zeversolar.py` | 5 | 0 | — |
| `localvolts_api.py` | 2 | 0 | — |
| `number.py` | 1 | 0 | — |
| `octopus_api.py` | 3 | 0 | — |
| `octopus_sessions.py` | 3 | 0 | — |
| `optimization/battery_controller.py` | 8 | 0 | — |
| `optimization/battery_optimizer.py` | 2 | 0 | — |
| `optimization/coordinator.py` | 24 | 6 | 416,860,1540,1619,2555,2636 |
| `optimization/ev_coordinator.py` | 6 | 1 | 916 |
| `optimization/executor.py` | 1 | 0 | — |
| `optimization/load_estimator.py` | 6 | 1 | 1190 |
| `powerwall_local/client.py` | 9 | 0 | — |
| `powerwall_local/coordinator.py` | 0 | 1 | 173 |
| `powerwall_local/curtailment_fallback.py` | 4 | 2 | 209,286 |
| `powerwall_local/dispatch.py` | 1 | 0 | — |
| `powerwall_local/fleet_api_bms.py` | 1 | 0 | — |
| `powerwall_local/pairing.py` | 2 | 0 | — |
| `powerwall_local/signaling.py` | 6 | 2 | 791,797 |
| `powerwall_local/transport.py` | 9 | 0 | — |
| `powerwall_local/views.py` | 12 | 5 | 237,252,538,1022,1036 |
| `select.py` | 1 | 0 | — |
| `sensor.py` | 4 | 4 | 2094,2125,3655,3705 |
| `sigenergy_api.py` | 8 | 0 | — |
| `sigenergy_charger.py` | 4 | 0 | — |
| `switch.py` | 5 | 0 | — |
| `tariff_converter.py` | 6 | 0 | — |
| `tariff_utils.py` | 3 | 0 | — |
| `update.py` | 2 | 0 | — |
| `websocket_client.py` | 6 | 0 | — |
| `zaptec_api.py` | 1 | 0 | — |

> Note: prior count of 940 was inflated. Actual scan: **178 broad+silent** across 88 production files (scanner counts lines matching `except Exception`/`except:` per distinct `except` clause). The 940 figure likely double-counted or included test files.

---

## CAT2: `Any` usages — 1,001 total across 60 files

| File | Count |
|---|---|
| config_flow.py | 150 |
| sensor.py | 83 |
| automations/actions.py | 72 |
| coordinator.py | 67 |
| automations/loadpoint_status.py | 56 |
| __init__.py | 55 |
| switch.py | 49 |
| automations/ev_ownership.py | 48 |
| automations/triggers.py | 38 |
| inverters/neovolt.py | 35 |
| tariff_converter.py | 34 |
| optimization/coordinator.py | 28 |
| powerwall_local/bms_health.py | 27 |
| automations/ev_charging_planner.py | 22 |
| automations/__init__.py | 20 |
| aemo_api.py | 13 |
| auto_update.py | 12 |
| octopus_api.py | 12 |
| powerwall_local/client.py | 10 |
| flow_power_pricing.py | 9 |
| optimization/load_estimator.py | 7 |
| sigenergy_model.py | 7 |
| solar_surplus_config.py | 7 |
| tariff_time.py | 7 |
| automations/observed_tesla_sessions.py | 6 |
| currency.py | 6 |
| optimization/ev_coordinator.py | 6 |
| optimization/executor.py | 6 |
| powerwall_local/pairing.py | 6 |
| tariff_templates.py | 6 |
| inverters/solax_battery.py | 5 |
| powerwall_local/signaling.py | 5 |
| automations/generic_charger_soc.py | 4 |
| automations/weather.py | 4 |
| binary_sensor.py | 4 |
| flow_power_portal.py | 4 |
| inverters/foxess.py | 4 |
| inverters/foxess_entity.py | 4 |
| octopus_sessions.py | 4 |
| optimization/schedule_reader.py | 4 |
| powerwall_local/dispatch.py | 4 |
| powerwall_local/transport.py | 4 |
| powerwall_local/views.py | 4 |
| update.py | 4 |
| websocket_client.py | 4 |
| automations/ev_pricing.py | 3 |
| automations/live_status.py | 3 |
| button.py | 3 |
| inverters/fronius_reserva.py | 3 |
| inverters/saj_h2.py | 3 |
| localvolts_api.py | 3 |
| sigenergy_api.py | 3 |
| zaptec_api.py | 3 |
| inverters/goodwe_battery.py | 2 |
| optimization/battery_optimizer.py | 2 |
| powerwall_local/coordinator.py | 2 |
| powerwall_local/curtailment_fallback.py | 2 |
| automations/ev_charging_session.py | 1 |
| number.py | 1 |
| powerwall_local/tesla_local_pb2.py | 1 |

---

## CAT3: Functions missing return type — 926 total across 75 files

| File | Missing |
|---|---|
| __init__.py | 181 |
| config_flow.py | 109 |
| automations/actions.py | 75 |
| sensor.py | 67 |
| automations/ev_charging_planner.py | 53 |
| coordinator.py | 41 |
| optimization/coordinator.py | 31 |
| inverters/neovolt.py | 26 |
| optimization/load_estimator.py | 24 |
| optimization/battery_optimizer.py | 21 |
| switch.py | 21 |
| tariff_converter.py | 17 |
| automations/triggers.py | 14 |
| automations/loadpoint_status.py | 12 |
| binary_sensor.py | 11 |
| automations/ev_charging_session.py | 10 |
| automations/ev_ownership.py | 10 |
| foxess_api.py | 9 |
| inverters/foxess.py | 8 |
| octopus_api.py | 8 |
| sigenergy_api.py | 8 |
| websocket_client.py | 8 |
| inverters/alphaess.py | 7 |
| number.py | 6 |
| powerwall_local/bms_health.py | 6 |
| select.py | 6 |
| inverters/fronius.py | 5 |
| inverters/sungrow_sh.py | 5 |
| powerwall_local/views.py | 5 |
| zaptec_api.py | 5 |
| alphaess_api.py | 4 |
| auto_update.py | 4 |
| inverters/enphase.py | 4 |
| inverters/goodwe.py | 4 |
| inverters/huawei.py | 4 |
| inverters/sigenergy.py | 4 |
| inverters/solax_battery.py | 4 |
| inverters/sungrow.py | 4 |
| inverters/zeversolar.py | 4 |
| octopus_sessions.py | 4 |
| optimization/executor.py | 4 |
| powerwall_local/transport.py | 4 |
| sigenergy_charger.py | 4 |
| aemo_api.py | 3 |
| automations/__init__.py | 3 |
| automations/weather.py | 3 |
| inverters/foxess_entity.py | 3 |
| inverters/solaredge.py | 3 |
| localvolts_api.py | 3 |
| optimization/ev_coordinator.py | 3 |
| powerwall_local/client.py | 3 |
| powerwall_local/pairing.py | 3 |
| tariff_utils.py | 3 |
| automations/observed_tesla_sessions.py | 2 |
| button.py | 2 |
| currency.py | 2 |
| flow_power_pricing.py | 2 |
| inverters/base.py | 2 |
| inverters/fronius_reserva.py | 2 |
| inverters/saj_h2.py | 2 |
| inverters/solax.py | 2 |
| optimization/battery_controller.py | 2 |
| powerwall_local/curtailment_fallback.py | 2 |
| powerwall_local/dispatch.py | 2 |
| sigenergy_model.py | 2 |
| update.py | 2 |
| const.py | 1 |
| epex_api.py | 1 |
| inverters/__init__.py | 1 |
| inverters/goodwe_battery.py | 1 |
| powerwall_local/coordinator.py | 1 |
| powerwall_local/fleet_api_bms.py | 1 |
| powerwall_local/signaling.py | 1 |
| solar_surplus_config.py | 1 |
| tariff_time.py | 1 |

---

## CAT4: `_LOGGER.info` in coordinator.py (88 calls) and sensor.py (37 calls)

### coordinator.py — 88 calls
- **One-shot/setup** (6): lines 1265, 1299, 1418, 1731, 1734, 1737
- **Recurring update path** (82): all other lines — see full list in scan output above

### sensor.py — 37 calls
All 37 are in `async_setup_entry` / sensor registration paths.
- **One-shot/setup** (35): lines 1398, 1408, 1418, 1439, 1519, 1531, 1543, 1555, 1566, 1578, 1590, 1628, 1632, 1644, 1657, 1670, 1686, 1716, 1724, 1749, 1763, 1777, 1850, 1852, 1873, 1899, 1903, 3600, 3622, 3643, 4478
- **Recurring** (2): lines 3780 (inverter poll every 30s), ~4500+, 4845

---

## CAT5: Blocking calls in async paths — 5 total

| File:Line | Type | Detail |
|---|---|---|
| `aemo_api.py:163` | `open()` | `with zf.open(csv_files[0]) as f:` (zipfile, not filesystem — likely ok) |
| `aemo_api.py:382` | `open()` | `with zf.open(csv_files[0]) as f:` (same) |
| `automations/ev_charging_session.py:345` | `open()` | `with open(self.storage_path, "r") as f:` — **blocking filesystem I/O in async context** |
| `automations/ev_charging_session.py:373` | `open()` | `with open(self.storage_path, "w") as f:` — **blocking filesystem I/O in async context** |
| `const.py:12` | `open()` | `with open(_MANIFEST_PATH) as f:` — at module import time, not async |

No `time.sleep` or `requests.` in production.

---

## CAT6: Hardcoded entity-ID literals — 56 total across 11 files

Full enumeration in scan output above. Key clusters:
- `coordinator.py`: 24 hits — Solcast sensor ID variants (3 fallback chains × 6 keys) + octopus wildcard
- `__init__.py`: 5 hits — Solcast fallbacks, charger switch, charger status
- `optimization/coordinator.py`: 6 hits — home load sensor fallback chain
- `automations/ev_charging_planner.py`: 4 hits — Powerwall/Sigenergy/Sungrow SOC fallbacks
- `optimization/load_estimator.py`: 6 hits — Solcast fallbacks
- `inverters/solax.py`: 2 hits — number/sensor entity IDs from external Solax integration
- `optimization/battery_controller.py`: 2 hits — own integration entities
- `select.py`: 2 hits — own integration entities
- `switch.py`: 3 hits — own integration entities
- `binary_sensor.py`: 1 hit — own integration entity
- `powerwall_local/curtailment_fallback.py`: 1 hit — own integration entity

---

## CAT7: `aiohttp.ClientTimeout` — 122 total

Distribution by value:
- `total=30` — 83 sites (dominant magic value, no named constant)
- `total=10` — 14 sites
- `total=15` — 10 sites
- `total=35` — 3 sites (powerwall_local)
- `total=12` — 1 site (`__init__.py:4842`)
- `total=60` — 1 site (`aemo_api.py:365`)
- `total=timeout_seconds` — 3 sites (parameterised — correct)
- `total=self.TIMEOUT_SECONDS` — 2 sites (enphase, zeversolar — correct)
- `total=timeout` — 1 site (octopus_api — correct)

Full file:line list in scan output above.

---

## CAT8: `asyncio.sleep` — 104 total

Magic-value clusters:
- `1` — 28 sites (inverter retry loops)
- `2` — 11 sites (charger step waits)
- `5` — 8 sites (various retry/startup)
- `3` — 7 sites (Tesla API retry)
- `0.5` — 5 sites (Modbus write delays)
- `0.1` — 5 sites (Fronius polling)
- `60` — 3 sites (optimization loops)
- `30` — 1 site (websocket reconnect)
- `300` — 1 site (EV coordinator — 5 min sleep in async loop)
- Named constants/expressions — 17 sites (correct)

Full file:line list in scan output above.

---

## CAT9: `hass.services.async_register` — 33 total

- **WITH schema**: 3 (all in `powerwall_local/services.py:140,144,151`)
- **WITHOUT schema**: 30 (all in `__init__.py` — lines 20455, 20456, 25588–25609, 25899–25900, 25989, 26620, 27647–27689)

All 30 unschema'd registrations in `__init__.py` pass no `vol.Schema`. Services include force_charge, force_discharge, hold_battery_soc, restore_normal, set_backup_reserve, set_operation_mode, set_grid_export, set_grid_charging, curtail_inverter, restore_inverter, and ~12 others.

---

## CAT10: `from typing import Any` — 59 files

Full list in scan output above. Matches prior count of 59.

---

## CAT11: TODO/FIXME/HACK/XXX/NOTE — 27 total across 9 files

All 27 are `NOTE` comments — zero `TODO`, `FIXME`, `HACK`, or `XXX`. Classification:

**Legitimate domain notes** (architectural/invariant explanations):
- `__init__.py:6789` — spike manager lifecycle note
- `__init__.py:8377` — unit conversion ambiguity (cents vs $/kWh)
- `__init__.py:18085` — AEMO mode lacks WebSocket
- `__init__.py:20923` — ordering constraint for restore_force_mode_from_persistence
- `__init__.py:25322` — Tesla API inverted logic for grid charging
- `automations/actions.py:3392` — Tesla 5A minimum enforcement
- `automations/actions.py:6034` — Tesla idle vs real max current
- `automations/ev_charging_planner.py:2852,4298,5164,5190,7294` — 5 domain invariant notes
- `const.py:1330,1388` — inverter hardware limitations
- `coordinator.py:482,1858,2310,7308,8078` — 5 domain/API notes
- `inverters/__init__.py:38,96` — inverter hardware notes
- `inverters/enphase.py:1655` — AGF profile limitation
- `sigenergy_api.py:793` — negative price handling difference
- `tariff_converter.py:978,1077,1133,2045` — 4 Tesla API constraint notes

**Deferred work**: NONE. No actionable deferred items.

---

## CAT12: Inverter method matrix

Methods present in 3+ inverter files (candidates for `base.py` abstract interface):

| Method | Count | Files |
|---|---|---|
| `__init__` | 19 | all |
| `connect` | 19 | all |
| `disconnect` | 19 | all |
| `get_status` | 18 | all except goodwe_battery |
| `curtail` | 16 | all except neovolt, saj_h2, foxess_entity, fronius_reserva |
| `restore` | 16 | same as curtail |
| `force_charge` | 10 | alphaess, foxess, foxess_entity, fronius_reserva, goodwe_battery, neovolt, saj_h2, sigenergy, solax_battery, sungrow_sh |
| `force_discharge` | 10 | same as force_charge |
| `restore_normal` | 10 | same |
| `get_backup_reserve` | 8 | foxess, foxess_entity, fronius_reserva, goodwe_battery, neovolt, sigenergy, solax_battery, sungrow_sh |
| `set_backup_reserve` | 8 | same |
| `set_charge_rate_limit` | 4 | foxess, foxess_entity, sigenergy, sungrow_sh |
| `set_discharge_rate_limit` | 4 | foxess, foxess_entity, sigenergy, sungrow_sh |
| `get_energy_summary` | 3 | alphaess, foxess, sigenergy |
| `set_idle` | 3 | fronius_reserva, neovolt, saj_h2 |

`connect`, `disconnect`, `get_status`, `curtail`, `restore` are universal and should be abstract on `base.py`.
`force_charge`, `force_discharge`, `restore_normal`, `get_backup_reserve`, `set_backup_reserve` are majority and should be abstract with `NotImplementedError` default.

---

## CAT13: Modules missing `_LOGGER` — 18 files (not 7)

| File | Lines | Risk |
|---|---|---|
| `automations/ev_ownership.py` | 362 | High — no error logging possible |
| `automations/loadpoint_status.py` | 798 | High — large stateful module |
| `const.py` | 2006 | Low — constants only |
| `tariff_templates.py` | 607 | Medium — complex logic |
| `powerwall_local/bms_health.py` | 333 | High — health calculations |
| `powerwall_local/tesla_local_pb2.py` | 999 | Low — generated protobuf |
| `powerwall_local/tedapi_combined_pb2.py` | 118 | Low — generated protobuf |
| `automations/ocpp_status.py` | 135 | Medium |
| `automations/generic_charger_soc.py` | 51 | Low |
| `automations/live_status.py` | 28 | Low |
| `currency.py` | 123 | Low |
| `flow_power_pricing.py` | 159 | Medium |
| `optimization/__init__.py` | 47 | Low |
| `optimization/schedule_reader.py` | 97 | Medium |
| `powerwall_local/__init__.py` | 38 | Low |
| `powerwall_local/exceptions.py` | 21 | Low |
| `sigenergy_model.py` | 53 | Low |
| `solar_surplus_config.py` | 72 | Low |

---

## CAT14: Test file classification (63 files)

| File | Lines | Category |
|---|---|---|
| conftest.py | 25 | trivial |
| test_ac_inverter_model_options.py | 67 | behavioural |
| test_amber_price_cache.py | 416 | behavioural |
| test_auto_schedule_settings.py | 136 | behavioural |
| test_auto_update.py | 294 | behavioural |
| test_automation_state_coordinators.py | 35 | source-text-ast |
| test_battery_controller_wrapper.py | 147 | behavioural |
| test_battery_export_allowed_slots.py | 2287 | behavioural |
| test_battery_optimizer_export_guard.py | 1145 | behavioural |
| test_calendar_history_energy_summary.py | 221 | source-text-ast |
| test_config_flow_weather_options.py | 857 | source-text-ast |
| test_currency.py | 63 | behavioural |
| test_currency_sensor_metadata.py | 596 | behavioural |
| test_currency_tariffs.py | 119 | behavioural |
| test_dashboard_dependency_detection.py | 82 | behavioural |
| test_dispatch.py | 265 | behavioural |
| test_dt_util_scope.py | 44 | source-text-ast |
| test_energy_flow_frontend.py | 55 | behavioural |
| test_ev_active_charging.py | 135 | behavioural |
| test_ev_ocpp_actions.py | 2262 | behavioural |
| test_ev_ownership_runtime.py | 166 | behavioural |
| test_ev_price_level_ownership.py | 1398 | behavioural |
| test_ev_pricing.py | 78 | behavioural |
| test_ev_vehicle_status.py | 310 | behavioural |
| test_flow_power_pricing_regressions.py | 111 | source-text-ast |
| test_force_mode_controls.py | 569 | source-text-ast |
| test_forecast_discrepancy.py | 85 | behavioural |
| test_foxess_cloud_api.py | 236 | behavioural |
| test_foxess_entity_controller.py | 431 | behavioural |
| test_foxess_entity_startup_retry.py | 81 | source-text-ast |
| test_foxess_modbus_imports.py | 162 | behavioural |
| test_fronius_reserva_controller.py | 260 | behavioural |
| test_inverter_status_sensor.py | 31 | source-text-ast |
| test_live_status.py | 47 | behavioural |
| test_load_estimator.py | 292 | behavioural |
| test_loadpoint_status.py | 703 | behavioural |
| test_neovolt_battery_controller.py | 1553 | behavioural |
| test_number_startup_delay.py | 152 | source-text-ast |
| test_observed_tesla_sessions.py | 175 | behavioural |
| test_ocpp_status.py | 76 | behavioural |
| test_octopus_sessions.py | 178 | behavioural |
| test_optimization_enabled_switch.py | 68 | behavioural |
| test_optimization_ev_coordinator_ownership.py | 419 | behavioural |
| test_optimization_price_source.py | 712 | source-text-ast |
| test_powerwall_bms_health.py | 401 | behavioural |
| test_powerwall_local_dcq_snapshot.py | 199 | behavioural |
| test_powerwall_signaling.py | 110 | behavioural |
| test_saj_h2_controller.py | 559 | behavioural |
| test_sigenergy_automation_actions.py | 28 | trivial |
| test_sigenergy_controller.py | 244 | behavioural |
| test_sigenergy_ev_charger.py | 298 | behavioural |
| test_sigenergy_tariff_conversion.py | 438 | behavioural |
| test_solar_surplus_config.py | 87 | behavioural |
| test_solaredge_controller.py | 101 | behavioural |
| test_solax_battery_controller.py | 364 | behavioural |
| test_solcast_settings.py | 153 | source-text-ast |
| test_spike_alert_time.py | 75 | behavioural |
| test_sungrow_curtailment_runtime.py | 92 | source-text-ast |
| test_sungrow_inverter_controller.py | 127 | behavioural |
| test_sungrow_sh_controller.py | 591 | behavioural |
| test_tariff_time.py | 161 | behavioural |
| test_tesla_grid_mode_switches.py | 344 | behavioural |
| test_zaptec_api.py | 47 | behavioural |

Summary: 46 behavioural, 13 source-text-ast, 2 trivial, 0 mock-only

---

## CAT15: `Store(` usage — 16 total

| File:Line | Version | Key |
|---|---|---|
| `__init__.py:14976` | 1 | `"lovelace_dashboards"` — no entry_id, shared |
| `__init__.py:16229` | 1 | `f"{DOMAIN}.fp_session.{entry.entry_id}"` |
| `__init__.py:16419` | `STORAGE_VERSION` | `f"{STORAGE_KEY}.{entry.entry_id}"` |
| `__init__.py:26987` | 1 | `f"{DOMAIN}.fp_session.{entry.entry_id}"` |
| `__init__.py:27240` | — | `AutomationStore(hass)` — wrapper |
| `__init__.py:28789` | 1 | `f"{DOMAIN}.fp_session.{entry.entry_id}"` |
| `auto_update.py:443` | (see file) | last_run key |
| `automations/__init__.py:30` | `STORAGE_VERSION` | `STORAGE_KEY` |
| `coordinator.py:145` | (see file) | — |
| `coordinator.py:1240` | `USAGE_STORAGE_VERSION` | `f"{USAGE_STORAGE_KEY}.{entry_id}"` |
| `coordinator.py:1722` | (see file) | lifetime totals |
| `coordinator.py:5178` | 1 | `f"{DOMAIN}.foxess_cloud.{entry_id}"` |
| `coordinator.py:6536` | 1 | `f"{DOMAIN}_solcast_rate_limit"` — global, no entry_id |
| `coordinator.py:6537` | 1 | `f"{DOMAIN}_solcast_forecast_cache"` — global, no entry_id |
| `coordinator.py:8377` | 1 | `f"power_sync.flow_power_twap.{entry_id}"` |
| `optimization/coordinator.py:237` | (see file) | cost store |

Notes:
- `lovelace_dashboards` (line 14976) and the two Solcast stores (6536, 6537) use version=1 with no migration path.
- `fp_session` store is instantiated in 3 separate places (16229, 26987, 28789) — potential triple-write conflict.
- Most stores use hardcoded version=1 with no migration handler.

---

## CAT16: `unique_id` without `entry_id` prefix

Only 2 hits — both are comments in `inverters/saj_h2.py:82,139` documenting the external `stanus74` integration's ID format. No actual unique_id assignments without entry_id prefix found in production code.

---

## CAT17: `async_on_unload` / `async_on_remove` — 11 total

| File:Line | Call |
|---|---|
| `__init__.py:28123` | `entry.async_on_unload(entry.add_update_listener(...))` |
| `sensor.py:2504` | `self.async_on_remove(...)` |
| `sensor.py:4459` | `self.async_on_remove(...)` |
| `sensor.py:4482` | `self.async_on_remove(...)` |
| `switch.py:599` | `self.async_on_remove(...)` |
| `switch.py:784` | `self.async_on_remove(...)` |
| `switch.py:968` | `self.async_on_remove(...)` |
| `switch.py:1081` | `self.async_on_remove(...)` |
| `switch.py:1140` | `self.async_on_remove(...)` |
| `switch.py:1199` | `self.async_on_remove(...)` |
| `switch.py:1521` | `self.async_on_remove(...)` |

Notable gaps: `coordinator.py`, `optimization/coordinator.py`, `powerwall_local/coordinator.py`, `websocket_client.py` — none register cleanup callbacks for sessions/connections.

---

## CAT18: `HomeAssistantView` subclasses — 74 total

**MISSING `requires_auth`** (6 views — security gap):
- `__init__.py:14128` — `AutoScheduleSettingsView`
- `__init__.py:14363` — `PriceLevelChargingSettingsView`
- `__init__.py:14508` — `ScheduledChargingSettingsView`
- `__init__.py:14696` — `HomePowerSettingsView`
- `powerwall_local/views.py:476` — `PowerwallSetGatewayIpView`
- `powerwall_local/views.py:819` — `PowerwallGatewayInfoView`
- `powerwall_local/views.py:946` — `PowerwallDiscoverView`
- `powerwall_local/views.py:1053` — `PowerwallSafetyConfigView`
- `powerwall_local/views.py:1129` — `PowerwallCurtailmentFallbackView`

All others: `requires_auth = True`. Prior count of 61 was wrong — actual: **74**.

---

## CAT19: `print(` in production — 2 total

Both in `tariff_utils.py:5,23` — intentional suppression of `print()` from `aemo_to_tariff` library. Not new `print()` calls.

---

## CAT20: Dangerous calls — 0

No `eval`, `exec`, `pickle`, `subprocess`, `os.system`, or `shell=True` in production code.

