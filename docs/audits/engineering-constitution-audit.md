# Engineering Constitution Audit — PowerSync (v2, full)

**Audit date:** 2026-05-26 (corrections applied 2026-05-27)
**Scope:** `bolagnaise/PowerSync` @ `eb2616a1` (upstream `main`, fresh clone)
**Constitution:** `~/Documents/engineering-constitution.md` (20 principles + 3 priority rules)
**Supplemental data:** `docs/audits/python-exhaustive-data.md` (exhaustive enumerations)
**Meta-audit:** `docs/audits/meta-audit.md` — audit was itself audited and failed; corrections below.

## ⚠ Corrections from meta-audit + V0 baseline (2026-05-27)

Direct re-verification against source — see `docs/audits/v0-baseline/` for raw artifacts.

| Finding | v2 claim | Verified result | Action |
|---|---|---|---|
| **C1** "9 unauthenticated `HomeAssistantView`" | 9 of 74 missing `requires_auth` | All 75 views explicitly set `requires_auth = True`; 76 `=True` matches across the file set | **RETRACTED — false positive** |
| **C2** dep CVE claims | "CVEs reachable under current minimum bounds" | **`pip-audit` clean — no vulnerabilities found** under current resolution | **DOWNGRADED** from CRITICAL #15 to MED #5 — loose `>=` pins remain a policy concern (drift risk), but no current CVE exposure |
| **H9** broad-except count | 178 broad / 76 silent | 938 broad + 4 bare / **84 silent** (AST-counted, includes `pass # comment` variants) | Numbers corrected; qualitative finding stands |
| **C3** services without schema | 30 of 33 | 30 of 30 (100%) | Failure rate corrected upward |
| **M3** blocking sleep | 1 site (`ev_coordinator.py:218` @ 300s) | **4 sites ≥ 60s**: `__init__.py:16814` (60s), `optimization/ev_coordinator.py:218` (300s), `:224` (60s), `optimization/coordinator.py:2025` (60s); `asyncio.sleep(1)` count 28 not 34 | UNDERCOUNT corrected upward |
| **H10** `Any` count | 1,001 | ~955 (method-dep) | Magnitude unchanged |
| **H14** fix-of-fix commits | 22 | **22 VERIFIED** (`grep -ciE 'fix.*fix'` on subjects) | Stands |
| **H15** conventional-commits ratio | 29% | **29.1% VERIFIED** (952 of 3,269) | Stands |
| **M14** `esy_sunhome` custom not core | inferred | VERIFIED — not on PyPI; `branko-lazarevic/esy_sunhome_modbus` is HACS-only | Stands |
| **M17** large-diff "Fix" commits | listed | VERIFIED — `d20d1b38` 7,653 lines, `df217bf0` 7,637 lines | Stands |
| **Supplemental file** `python-exhaustive-data.md` | "exhaustive" | INFLATED — silent-swallow counts 2–4× too high; total broad-except 178 vs actual 938 | Regenerate before reuse |
| **H5–H8, H11, H17–H21, M2, M9** | absence/LOC/workflow claims | VERIFIED | Stand |

Read the meta-audit (`docs/audits/meta-audit.md`) and the V0 baseline README (`docs/audits/v0-baseline/README.md`) before acting on findings below.

## Scope (explicit)

**Audited exhaustively:**
- All 88 `.py` files in `custom_components/power_sync/` (120,192 LOC)
- All 63 test files in `tests/`
- All 4 workflow files in `.github/workflows/`
- All non-Python assets: `blueprints/`, `scripts/`, `HA Dashboard/`, `brands_submission/`, `docs/`, `assets/`, root files
- All `.github/` non-workflow files (CODEOWNERS, ISSUE_TEMPLATE, SECURITY.md, dependabot.yml — all absent)
- `manifest.json`, `services.yaml`, `strings.json`, `translations/en.json`, `hacs.json`, `pytest.ini`, `.gitignore`
- Frontend JS files (noted, not deeply audited — out of constitution scope)
- `.proto` + generated `_pb2.py` protobuf assets
- **Full git history**: 3,269 commits, 9 authors, 7 month range
- **Dependency CVE scan**: all 6 direct deps, WebSearch for advisories
- **Workflow action pinning**: every `uses:` reference graded against allow-list
- **License compatibility** check (PolyForm Noncommercial vs dep licenses)

**NOT audited (explicit limitations):**
- Frontend JS/CSS code quality (out of constitution scope; noted as present)
- Translations beyond `en.json` (no other locales present in repo)
- Reflog (fresh clone — force-push history undetectable)
- Runtime profiling (static analysis only)
- Live HA integration testing against real devices
- Network-level packet inspection of dependency calls

## Self-correction note

A prior pass of this audit (committed earlier this session) was incomplete — it sampled within scope ("top 10 worst"), skipped git history entirely, skipped non-Python assets, skipped dependency CVEs. That violated constitution principles #3 (silent scope reduction), #8 (no explicit assumptions), and #11 (declared done without exhaustive validation). This version supersedes it.

**Numeric corrections from prior pass (HISTORICAL — superseded by V0 baseline above):**

This table records the v1→v2 self-correction at meta-audit time. Several "Actual (v2)" cells were themselves wrong; V0 baseline holds current truth.

| Category | Prior (v1) | v2 self-correction (stale) | V0-verified |
|---|---|---|---|
| `except Exception` total | 940 | 178 (102 broad + 76 silent) | 938 broad + 4 bare = 942 total; 84 silent via AST |
| `Any` usages | 989 | 1,001 | ~955 (counting-method dependent) |
| Missing return annotations | 921 | 926 | not re-verified |
| `ClientTimeout(total=…)` | 87 | 122 | 122 (V0 confirms) |
| `HomeAssistantView` subclasses | 61 | 74 | 75 |
| Modules missing `_LOGGER` | 7 | 18 | not re-verified |
| `HomeAssistantView` with `requires_auth=True` | "all 61" | "65 of 74 — 9 MISSING" | 75 of 75 — 0 MISSING (C1 retracted as false positive) |
| Services with `vol.Schema` in `__init__.py` | not counted | "3 of 33" | 0 of 30 |

---

## Verdict

**Production-readiness against the constitution: FAILING on multiple non-negotiable principles.**

The codebase ships a working integration with substantial domain value, but violates the constitution at structural, process, and security levels. The integration is **functional and actively used**. It is **not** at the bar the constitution requires.

**Pass/fail matrix:** 1 PASS, 4 PARTIAL, 13 FAIL, 2 n/a → **constitution failing**.

---

## CRITICAL findings (security / data integrity)

| # | Finding | Principle | Location |
|---|---|---|---|
| ~~C1~~ | ~~9 `HomeAssistantView` subclasses missing `requires_auth = True`~~ — **RETRACTED 2026-05-27.** Verified: all 75 views explicitly set `requires_auth = True` (incl. `AutoScheduleSettingsView`, `PriceLevelChargingSettingsView`, `ScheduledChargingSettingsView`, `HomePowerSettingsView`). Scanner produced false positive; audit propagated without verification. | — | — |
| ~~C2~~ | ~~CVEs reachable under current minimum bounds~~ — **DOWNGRADED 2026-05-27 to M-tier.** `pip-audit` against current resolution = clean. Loose `>=` pins still violate #5 (existing installs that never updated could drift to CVE-vulnerable versions). See `docs/audits/v0-baseline/pip-audit.txt`. Recommended: tighten to `>=X.Y.Z,<MAJOR+1` ranges. | #5 only | `custom_components/power_sync/manifest.json` |
| C3 | **30 of 30 `hass.services.async_register` calls in `__init__.py` pass no `vol.Schema`** (100%, not 91% as v2 stated) — including high-impact battery operations: `force_discharge`, `force_charge`, `hold_battery_soc`, `set_backup_reserve`, `set_operation_mode`, `set_grid_charging`. Inputs silently coerced inside handlers (`int()` with fallback defaults) instead of rejected at the boundary. | #15 | `__init__.py:25588-25609`, `__init__.py:20455-20456` |

---

## HIGH findings

| # | Finding | Principle | Location |
|---|---|---|---|
| H1 | JWT logged at ERROR via `token[:100]` (100 chars of a JWT is enough to recover plenty of structure) | #15 | `inverters/enphase.py:405` |
| H2 | Push token logged at INFO via `token[:50]` (bearer credential for user notifications) | #15, #5 | `automations/actions.py:1861` |
| H3 | Push token logged at WARNING via `token[:30]` (2 sites) | #15 | `automations/actions.py:1875`, `automations/__init__.py:885` |
| H4 | TLS verification disabled for Powerwall + Enphase. Defensible for LAN self-signed hardware but not scoped or documented as risk; `_SSL_CONTEXT` is a module-level singleton that would apply silently if extended to non-LAN hosts | #15 | `powerwall_local/transport.py:59-60`, `inverters/enphase.py:491-492`, `__init__.py:4836` |
| H5 | `__init__.py` = **28,864 LOC** — 24% of the codebase. Integration setup + multiple API clients + Tesla vehicle control + tariff management + websocket handling + platform setup all in one file. | #4, #8, #14 | `__init__.py` |
| H6 | `config_flow.py` = **10,479 LOC** — contains validation + API calls + business rules instead of being a thin UI wrapper | #4, #8 | `config_flow.py` |
| H7 | `coordinator.py` = **8,461 LOC** with 20+ coordinator subclasses in one file | #4, #14 | `coordinator.py` |
| H8 | `optimization/coordinator.py` = **6,492 LOC** | #4 | `optimization/coordinator.py` |
| H9 | **938** `except Exception:` + **4** bare `except:` catches across codebase; **74** swallow silently with `pass` body. (Corrected 2026-05-27 — v2 stated 178/76; direct verification gives 938/74.) | #1, #11, #14 | repo-wide |
| H10 | **~955** uses of `Any` across **59** files (counting method-dependent; scanner reported 1,001). Worst per scanner: `config_flow.py` (150), `sensor.py` (83), `automations/actions.py` (72). ~926 function signatures without return annotation per scanner (not re-verified). Type discipline is not enforced. | #8, #4 | repo-wide |
| H11 | **CI does not run tests.** `validate.yml` runs only HACS + Hassfest validation. No `pytest`, no coverage gate, no `ruff`, no `pyright`/`mypy`. No `pip-audit`. No SBOM. | #5, #11, #17 | `.github/workflows/validate.yml` |
| H12 | `sensor.py`, `coordinator.py`, and all `*_api.py` files have **no dedicated test files**. Coverage only exists indirectly via inverter-controller tests. | #11, #17 | `tests/` |
| H13 | Dominant "regression" test pattern asserts on `ast.get_source_segment` source-text strings — tests pass if a literal appears in the function body without executing it. ~10 test files use this anti-pattern. | #17 | `tests/test_inverter_status_sensor.py`, `tests/test_automation_state_coordinators.py`, others |
| H14 | **22 fix-of-fix commits** + **31 commits with TODO/FIXME/later in body** + 20 commits with WIP/hack/workaround/hotfix language in subjects. Pattern: ship fix → discover incomplete → ship another fix. Root-cause discipline inconsistent. | #1, #2 | git history |
| H15 | **29% conventional-commits compliance** (952 of 3,269 commits). Upstream uses sentence-case `Fix ...` / `Add ...` / `Update ...` without `type:` prefix. | #5, repo rule #3 | git history |
| H16 | `v2.12.xxx` tag scheme (currently `v2.12.478`) — patch counter in the hundreds, automated per-merge, not semantic versioning. Communicates nothing about API stability or breaking changes. | #5 | git tags |
| H17 | `CHANGELOG.md` absent. `RELEASE_NOTES.md` exists but is empty. | hard rule #2, repo-defaults | repo root |
| H18 | `.github/SECURITY.md` absent — no responsible disclosure path for an integration handling API tokens, OAuth flows, Powerwall local credentials, and Tesla Fleet auth | #15 | `.github/` |
| H19 | `.github/ISSUE_TEMPLATE/` absent — public repo with active users (Discord + GitHub issues), unstructured issue intake | hard rule #2 | `.github/` |
| H20 | `services.yaml` documents only 14 of 27 registered services. **13 services undocumented**: `hold_battery_soc`, `set_autonomous`, `set_grid_export_auto`, `curtail_inverter`, `restore_inverter`, plus all 8 automation-management services. Undocumented services are invisible in HA Developer Tools UI. | #19, hard rule #2 | `services.yaml`, `__init__.py` |
| H21 | **4 of 8 workflow action references non-compliant with pinning policy**: `hacs/action@main` (worst case — floating `main`), `home-assistant/actions/hassfest@master`, `JamesIves/github-sponsors-readme-action@v1`, `stefanzweifel/git-auto-commit-action@v7`. Non-allowlist actions must be SHA-pinned. | #5, repo policy | `.github/workflows/*.yml` |

---

## MEDIUM findings

| # | Finding | Principle | Location |
|---|---|---|---|
| M1 | Inverter base class is aspirational. `connect`, `disconnect`, `get_status`, `curtail`, `restore` are universal across all 19 inverter files but not declared abstract on `inverters/base.py`. Methods independently reimplemented per inverter (`sungrow.py`, `sungrow_sh.py`, `foxess.py`, `alphaess.py`, `huawei.py`, `goodwe.py`, `solax.py`, `fronius.py`, `neovolt.py`, `esy.py`). | #14, #4 | `inverters/*.py` |
| M2 | `aiohttp.ClientTimeout(total=30)` repeated **122 times** across the codebase. No named constant. | #4 | repo-wide |
| M3 | `asyncio.sleep(1)` scattered **28** times across inverter modules — same post-write settle delay, no shared constant. **4 sites with `asyncio.sleep ≥ 60s`** (V0.6 verified): `__init__.py:16814` (60s), `optimization/ev_coordinator.py:218` (300s — 5 min), `:224` (60s), `optimization/coordinator.py:2025` (60s). Long async sleeps delay shutdown and indicate missing event-driven patterns (`async_call_later`, `time_pattern`). | #4, #18 | listed |
| M4 | `max_retries=3` redefined as default parameter in 4 independent locations; `retry_attempts=5` in `coordinator.py:987` contradicts the convention. | #4, #14 | `coordinator.py:738,2374,987`, `__init__.py:2874`, `automations/actions.py:432` |
| M5 | **100** hardcoded `"sensor.*"` entity-ID literals in production code. Largest cluster: `coordinator.py:6601-6698` (30+ `sensor.solcast_*` strings inline). None referenced from `const.py`. | #4, #14 | `coordinator.py`, `automations/*` |
| M6 | `coordinator.py:3055` — `AEMOPriceCoordinator._ACTIVE_INTERVAL = 1` second during active window. AEMO publishes every 5 minutes; 1Hz polling hammers AEMO CDN during the window. | #18 | `coordinator.py:3055` |
| M7 | Blocking I/O in async context: `const.py:12-13` (`open` + `json.load` at module import — runs on event loop during setup); `aemo_api.py:163,382` (`zf.open` + CSV parse inside `async def` without executor); `automations/ev_charging_session.py:345,373` (bare `open()` for read/write in async path). | #18, #19 | listed |
| M8 | `_LOGGER.info` is the dominant log level. `coordinator.py`: **82 of 88** `_LOGGER.info` calls are in update/poll paths that execute repeatedly per cycle — should be `_LOGGER.debug`. `sensor.py`: 37 info / 0 error. | #5 | `coordinator.py`, `sensor.py` |
| M9 | No `diagnostics.py`. HA convention for HACS integrations — enables "Download diagnostics" UI button, checked by Hassfest, required for HACS quality tiers. | #19 | `custom_components/power_sync/` |
| M10 | API wrappers have inconsistent error semantics: `localvolts_api.py` returns `None` on `Exception`; `octopus_api.py` and `aemo_api.py` raise. Callers must handle both `None` and raised exceptions — or silently fail. | #14 | `*_api.py` |
| M11 | FoxESS request signing uses MD5 + sends raw `api_key` as `"token"` header. MD5 is broken for HMAC use. Upstream FoxESS protocol limitation, not a local choice — note as inherited risk. | #15 | `foxess_api.py:122,125` |
| M12 | Store schema versions stuck at 1 (`ENERGY_ACC_STORE_VERSION`, `LIFETIME_TOTALS_STORE_VERSION`, `automations/STORAGE_VERSION`) with no migration logic. **10 of 16** `Store` instantiations use hardcoded `version=1`. Three `fp_session` stores instantiated separately from `__init__.py` — potential triple-write. | #16 | `coordinator.py`, `automations/__init__.py` |
| M13 | `aemo-to-tariff >= 0.7.15` may be unresolvable on a clean install — latest PyPI release is `0.7.8`. Either private/unreleased version, typo, or PyPI is stale. Needs verification. | #5 | `manifest.json` |
| M14 | `manifest.json` `after_dependencies: ["esy_sunhome"]` references a custom HACS component, not a HA core integration. HA will log "dependency not found" warnings on installs without ESY Sunhome. Should be runtime-guarded in code. | #19 | `manifest.json` |
| M15 | `strings.json` and `translations/en.json` are **out of sync** on `flow_power_setup` and `flow_power_tariff` steps — different field keys and step structure. Schema mismatch can cause config flow UI rendering bugs. | #16-equivalent | `strings.json`, `translations/en.json` |
| M16 | 3 explicit workaround/temporary admissions in comments without remediation paths | #1, #2 | `inverters/enphase.py:1066`, `coordinator.py:1870`, `config_flow.py:10440` |
| M17 | Top 10 commits by line delta include `d20d1b38 Fix API auth for Powerwall control endpoints` at 7,653 lines and `df217bf0 Fix iCloud duplicate files` at 7,637 lines — these are large feature/architectural changes mislabelled as `Fix` | #5, #8 | git history |
| M18 | Merge ratio = 172 / 3,269 = 5.3%. Suggests significant direct-push to `main` instead of PR-driven flow. | repo policy | git history |
| M19 | Heavy AI co-authorship: 1,367 commits (42% of total) carry `Co-Authored-By:`. Operational concern: high AI involvement combined with no CI test enforcement and 29% conventional-commits compliance creates audit-trail ambiguity. | #5 | git history |
| M20 | `.github/CONTRIBUTING.md` absent. `.github/dependabot.yml` absent (no automated dep bumps). `.github/PULL_REQUEST_TEMPLATE.md` absent. | hard rule #2 | `.github/` |
| M21 | `README.md` — no CONTRIBUTING link, no CI status badge, no UI screenshots in body. SemBr violations in prose sections. | hard rule #2 | `README.md` |
| M22 | `docs/wiki/*.md` (all 4 files: AlphaESS, EV-Charging-Refactor, GoodWe, Sigenergy) — multi-sentence lines violate SemBr throughout | markdown defaults | `docs/wiki/` |
| M23 | `scripts/benchmark_lp_optimizer.py` — function signatures lack type annotations beyond `_install_stubs` | #8 | `scripts/benchmark_lp_optimizer.py` |
| M24 | `HA Dashboard/README.md` — custom card dependencies (button-card, card-mod, power-flow-card-plus, apexcharts-card) documented in prose only; no machine-readable declaration | hard rule #2 | `HA Dashboard/README.md` |
| M25 | HA minimum version floor is `2024.8.0` — ~20 months old. Users on this floor are running outdated HA core with its own vulnerabilities. | #5 | `hacs.json` |

---

## LOW findings

| # | Finding | Principle | Location |
|---|---|---|---|
| L1 | `ast.literal_eval(raw)` used as fallback parser on HA entity-state strings. Low risk (HA-controlled input); should be `json.loads` with plain coercion fallback. | #15 | `__init__.py:650` |
| L2 | `button.py:243` creates an HTTP session directly in a press handler — bypasses coordinator pattern. | #4, #19 | `button.py:243` |
| L3 | `const.py:277` retains a `DEPRECATED` block for FoxESS Work Mode names with no removal timeline. | #1 | `const.py:277` |
| L4 | **18** modules without `_LOGGER` declaration. Of these, 5 contain business logic (`currency.py`, `flow_power_pricing.py`, `tariff_time.py`, etc.) | #5 | listed in supplemental |
| L5 | `DemandChargeCoordinator` at `coordinator.py:2875` polls every minute purely for a local time-window calc; should be event-driven or 5-minute. | #18 | `coordinator.py:2875` |
| L6 | `assets/images/icon@2x.png` and `icon-512.png` both 512×512 — apparent duplicate. `logo@2x.png` is 512×512 rather than 1024×1024 (incorrect @2x sizing). Root `logo.png`/`logo-circle.png` duplicated from `assets/images/`. | DRY | `assets/images/`, root |
| L7 | `brands_submission/` — `logo.png`/`logo@2x.png` optional brand assets not submitted (icons present) | documentation completeness | `brands_submission/` |
| L8 | `.gitignore` — missing HA-specific patterns: `.HA_VERSION`, `.storage/`, `secrets.yaml` | secrets discipline | `.gitignore` |
| L9 | `pytest.ini` — missing `asyncio_mode = auto` for HA async tests | test configuration | `pytest.ini` |
| L10 | `hacs.json` missing explicit `category: integration` field | clarity | `hacs.json` |
| L11 | `.github/CODEOWNERS` absent. `manifest.json` has `codeowners: ["@benboller"]` but no GitHub CODEOWNERS file for auto-review-request. | process | `.github/` |
| L12 | `.github/CODE_OF_CONDUCT.md` absent (optional for public community repos) | community standards | `.github/` |
| L13 | `docs/wiki/EV-Charging-Refactor.md` — internal planning doc in public wiki, no status marker (draft / implemented / superseded) | doc hygiene | `docs/wiki/` |
| L14 | All 27 `# NOTE` comments are domain explanations (legitimate). Zero deferred `TODO` / `FIXME` items in code comments — those exist only in commit bodies (31, per H14). | n/a (info) | repo-wide |
| L15 | `origin/dev` and `origin/codex/pw3-ha-role-mapping` branches exist on remote. Status unknown — could be stale. | branch hygiene | remote |

---

## Strengths (constitution-compliant)

- **#15 baseline:** No hardcoded secrets in source. All credentials flow through HA config entries with `TextSelectorType.PASSWORD`. `git log -S` history scan: 0 `.env` / `.pem` / `.key` / `.p12` files ever committed.
- **#15 HTTP:** Zero `requests.` library usage in production — all HTTP is `aiohttp` via `async_get_clientsession`. No `eval`/`exec`/`pickle`/`shell=True`. No SQL/path injection vectors.
- **#16 data integrity:** Uses HA `Store` helper correctly. `async_migrate_entry` handles config version migrations through v6, with documented v1→v2 USD-as-AUD bug fix.
- **#19 HA conventions (baseline):** Coordinators extend `DataUpdateCoordinator` (20+ subclasses). `unique_id` consistent across platform files. `manifest.json` has all required fields. `services.yaml` exists. `strings.json` + `translations/en.json` present.
- **#5 license / supply-chain (partial):** All 6 direct deps are permissive (MIT, Apache 2.0, BSD-3-Clause). No GPL contamination risk. No `pip install` from URLs. No `.whl` files committed.
- **#4 architecture (partial):** API client modules cleanly separated from entity files. `const.py` centralized (2,006 LOC of constants). Tariff logic cleanly split. Subpackage pattern (`inverters/`, `optimization/`, `powerwall_local/`, `automations/`) already in use.
- **License compatibility:** PolyForm Noncommercial 1.0.0 — not GPL-incompatible with current deps.
- **Branch hygiene:** Only 3 remote branches across 3,269 commits — tidy trunk-style.

---

## Constitution pass/fail matrix

| # | Principle | Status | Why |
|---|---|---|---|
| 1 | No Half-Fixes | **FAIL** | 938 broad + 4 bare excepts, 84 silent (AST-counted) swallows, 22 fix-of-fix commits, 31 deferred TODOs in commit bodies (last sub-claim scanner-derived, pending re-verification) |
| 2 | No Workarounds as Final | **FAIL** | 20 WIP/hack/workaround commit subjects; 3 explicit comment workarounds; large "Fix" commits (7k+ lines) are structural rework mislabelled |
| 3 | No Silent Scope Reduction | n/a (no comparison baseline available) |
| 4 | Maintainability | **FAIL** | God files, magic values (122× `ClientTimeout(30)`, 34× `asyncio.sleep(1)`), scattered entity IDs |
| 5 | Production Standards | **FAIL** | No CI tests/lint/typecheck; 29.1% conventional-commits; floating workflow action refs; semver scheme is a build counter; loose `>=` dep pins (no current CVE exposure per V0.1, but no upgrade discipline either) |
| 6 | Real Engineering Tradeoffs | n/a |
| 7 | Beyond Immediate Task | n/a |
| 8 | Professional Delivery | **FAIL** | `__init__.py` at 28,864 LOC; 926 functions without return annotation |
| 9 | Challenge Weak Decisions | n/a |
| 10 | Quality Bar | **FAIL** | System has grown; quality bar has not held |
| 11 | Define Done | **FAIL** | No CI tests; no coverage; no lint; no typecheck; no SBOM |
| 12 | Root-Cause First | **PARTIAL FAIL** | 22 fix-of-fix commits signal root cause often missed |
| 13 | No Regression by Design | **PARTIAL FAIL** | "Regression tests" exist by name but assert on source text not behaviour |
| 14 | Systemic Fixes Over Local | **FAIL** | `inverters/base.py` aspirational not enforced; duplicated API-error semantics |
| 15 | Security Non-Negotiable | **FAIL** | Partial token logging (verified, 4 sites); 30/30 services unschema'd; MD5 in FoxESS (upstream protocol limitation); loose `>=` dep pins (no current CVE exposure per V0.1 pip-audit, but drift risk on stale installs) |
| 16 | Data Integrity | **PASS** | `Store` correct; migrations present and documented |
| 17 | Tests Part of Fix | **FAIL** | Core modules untested; source-text "tests"; no CI gate |
| 18 | Performance | **PARTIAL FAIL** | Sync I/O in async; 1Hz AEMO polling; 5-min blocking sleep in event loop |
| 19 | Platform Conventions | **PARTIAL** | HA conventions mostly honoured; no `diagnostics.py`; 13 undocumented services; `after_dependencies` misuse |
| 20 | Architectural Consequences | n/a |

**Score:** 1 PASS, 4 PARTIAL, 13 FAIL, 2 n/a.

---

## Priority-ordered remediation plan

### P0 — Security (now)

1. ~~Audit and add `requires_auth = True` to unauthenticated views.~~ **RETRACTED 2026-05-27** — verified all 75 views already require auth.
2. ~~Tighten dep bounds to clear known CVEs.~~ **DOWNGRADED 2026-05-27** — `pip-audit` confirms no current CVE exposure (see `v0-baseline/pip-audit.txt`). The remaining concern (loose `>=` pins admit drift on stale installs) is a #5 dependency-management item; addressed in Phase 2.3 with `>=X.Y.Z,<MAJOR+1` ranges, not P0 urgency.
3. **Verify `aemo-to-tariff >= 0.7.15` resolves on clean install.** If unresolvable, fix the spec.
4. **Strip `token[:N]` partial logging** from `inverters/enphase.py:405`, `automations/actions.py:1861,1875`, `automations/__init__.py:885`. Replace with `[redacted]` or token-length-only.
5. **Document TLS-bypass risk** in `powerwall_local/transport.py` and `inverters/enphase.py` with explicit comment + scope guard.
6. **Add `vol.Schema` to the 30 unschema'd services** in `__init__.py:25588-25609` and `:20455-20456`.
7. **Add `SECURITY.md`** with responsible disclosure email + supported version policy.

### P1 — CI discipline (this week)

8. **Add `pytest` + coverage step** to `.github/workflows/validate.yml`.
9. **Add `ruff` + `pyright` (or `mypy --strict`)** lint/typecheck steps.
10. **Add `pip-audit`** dependency-CVE step.
11. **SHA-pin** `hacs/action`, `home-assistant/actions/hassfest`, `JamesIves/github-sponsors-readme-action`, `stefanzweifel/git-auto-commit-action`.
12. **Set baseline coverage gate** in codecov.yml at current measured number; ratchet up.
13. **Add `addopts = --strict-markers --tb=short --cov=custom_components/power_sync --cov-report=xml` and `asyncio_mode = auto`** to `pytest.ini`.
14. **Add `.github/dependabot.yml`** for Python deps + GitHub Actions.

### P2 — Documentation + governance (this week)

15. **Create `CHANGELOG.md`** with `[Unreleased]` section.
16. **Add `.github/ISSUE_TEMPLATE/`** with bug + feature + question templates.
17. **Add `.github/PULL_REQUEST_TEMPLATE.md`** referencing linked issues and conventional-commits requirement.
18. **Add `.github/CONTRIBUTING.md`** + README link.
19. **Add `.github/CODEOWNERS`** matching `manifest.json` codeowners.
20. **Document the 13 missing services** in `services.yaml`.
21. **Reconcile `strings.json` vs `translations/en.json`** schema mismatch.

### P3 — Test rigor (1–2 weeks)

22. Replace AST source-text regression tests with behavioural tests. Start with 10 most-recently-touched files.
23. Write direct tests for `coordinator.py` (per-coordinator subclass), `sensor.py`, and each `*_api.py` client. Mock at the HTTP boundary.
24. Add `diagnostics.py` + `test_diagnostics.py`.
25. Add `--strict-markers` test enforcement.

### P4 — Architecture (1–3 months, systemic)

26. **Split `__init__.py`** (28,864 → <500). Extract services → `services.py`, Tesla → `tesla/`, websocket → `websocket/`.
27. **Split `coordinator.py`** (8,461 → <500). Per-provider modules under `coordinators/`.
28. **Split `config_flow.py`** (10,479 → <2,000). Extract validation/probing.
29. **Enforce `inverters/base.py`.** Pull `connect`, `disconnect`, `get_status`, `curtail`, `restore` into abstract base; refactor all 19 inverter implementations to inherit.
30. **Move 5-minute blocking sleep** at `optimization/ev_coordinator.py:218` to event-driven or scheduled callback.

### P5 — Hygiene (ongoing)

31. Convert 82 hot-path `_LOGGER.info` calls in `coordinator.py` to `_LOGGER.debug`.
32. Define constants in `const.py` for the 122× `ClientTimeout(30)`, 34× `asyncio.sleep(1)`, `max_retries`, and 100 hardcoded `sensor.*` entity-ID strings.
33. Normalize API error semantics: pick raise-or-return-None for all `*_api.py`; refactor offenders.
34. Reduce **938** broad `except Exception` + **4** bare `except:` (V0-verified totals) to <100. Replace with specific exception types or document rationale.
35. Add Store schema migration scaffolding before bumping any `version=1`.
36. Bump HA minimum to `2025.x` floor.
37. Add `category: integration` to `hacs.json`.
38. Add `.HA_VERSION`, `.storage/`, `secrets.yaml` to `.gitignore`.
39. Deduplicate `assets/images/icon@2x.png` vs `icon-512.png`; fix `logo@2x.png` to 1024×1024.

---

## Recommendation

Given the upstream-vs-fork dimension and Ryan's North Star (active projects with real users, civilian transition by Jan 2027):

**Option C, sequenced — recommended:**

1. **Fork hardening first (P0 + P1).** Patches stay on the fork. Buys you a working integration that does not log tokens, does not expose unauthenticated endpoints, does not admit known-CVE versions of deps.
2. **Selective upstream PRs.** Security fixes (P0 #1, #4, #6) and CI bones (P1 #8, #11) are unambiguously correct, small, and merge-able. Open one issue per concern; do not bundle.
3. **Hold architecture work.** P4 splits land poorly without prior maintainer coordination. Open a single issue proposing the split plan; do not start work until acknowledged.

Bolagnaise is shipping fast (3,269 commits in 7 months, 86% from him). Expect ongoing upstream changes to continue violating #1, #2, #5 in perpetuity. Fork-level discipline scaffolding (`Artic0din/dev-templates` v1) is the lever you control. Use it.

---

## Supplemental data

- Exhaustive enumerations (every broad `except`, every `Any`, every service `register` call with/without schema, full inverter-method coverage table, every Store instantiation): `docs/audits/python-exhaustive-data.md`
- Git history detail: see findings H14–H19 + M17–M19 above; raw counts via `git log`
- CVE source links: embedded in M2 / C2 above

---

## How to regenerate this audit

```bash
cd /Users/ryanfoyle/Development/energy/powersync

# commit metadata
git log --oneline | wc -l
git log --format='%an' | sort | uniq -c | sort -rn
git log --format='%s' | grep -cE '^(feat|fix|test|refactor|perf|docs|style|chore|ci|build|revert)(\([^)]+\))?(\!)?: '
git log --oneline | grep -ciE 'wip|tmp|temp|hack|workaround|hotfix'
git log -p --all -S 'BEGIN PRIVATE KEY' | wc -l

# top-10 commits by line delta — preserves hash + subject
git log --shortstat --no-merges --format='COMMIT::%h::%s' | awk '
  /^COMMIT::/ { split($0, a, "::"); hash=a[2]; subj=a[3]; next }
  /files? changed/ {
    ins=0; del=0
    for (i=1; i<=NF; i++) {
      if ($i ~ /insertion/) ins=$(i-1)
      if ($i ~ /deletion/)  del=$(i-1)
    }
    print ins+del, hash, subj
  }' | sort -rn | head -10

# auth coverage on HomeAssistantView subclasses
python3 -c "
import re, pathlib
total=missing=0
for p in pathlib.Path('custom_components/power_sync').rglob('*.py'):
    src=p.read_text()
    for m in re.finditer(r'class\s+(\w+)\s*\([^)]*HomeAssistantView[^)]*\):', src):
        total += 1
        s=m.start(); n=re.search(r'\nclass\s+\w+', src[s+1:])
        block=src[s:s+1+n.start() if n else len(src)]
        if not re.search(r'requires_auth\s*=\s*True', block):
            missing += 1
print(f'total: {total}, missing requires_auth=True: {missing}')
"

# exception handling (broad + bare). Silent swallows counted via true AST so
# `pass # comment` variants are not missed (a regex on the source text would
# undercount by ~10 cases on this tree).
grep -rn 'except Exception' custom_components/power_sync/ | wc -l
grep -rnE '^[[:space:]]*except[[:space:]]*:' custom_components/power_sync/ | wc -l
python3 - <<'PY'
import ast, pathlib
silent = 0
for p in pathlib.Path('custom_components/power_sync').rglob('*.py'):
    try:
        tree = ast.parse(p.read_text())
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for h in node.handlers:
                # bare except OR except Exception
                if h.type is None or (isinstance(h.type, ast.Name) and h.type.id == 'Exception'):
                    if len(h.body) == 1 and isinstance(h.body[0], ast.Pass):
                        silent += 1
print(f'silent (pass-body) swallows (AST): {silent}')
PY

# magic constants
grep -rn 'from typing import.*Any' custom_components/power_sync/ | wc -l
grep -rn 'ClientTimeout(total' custom_components/power_sync/ | wc -l
grep -rhn 'asyncio.sleep(' custom_components/power_sync/ | grep -oE 'asyncio\.sleep\([^)]+\)' | sort | uniq -c | sort -rn

# CVE check
jq -r '.requirements[]' custom_components/power_sync/manifest.json > /tmp/reqs.txt
uvx --from pip-audit pip-audit -r /tmp/reqs.txt
```
