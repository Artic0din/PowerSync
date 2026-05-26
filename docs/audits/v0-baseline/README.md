# V0 Baseline — Audit Verification Artifacts

**Date:** 2026-05-27
**Branch:** `chore/audit-v0-baseline`
**Repo state:** fresh clone of `bolagnaise/PowerSync` @ `eb2616a1` (upstream main)
**Purpose:** Verify the audit's unverified findings before executing the remediation plan. Per constitution #11 (Define Done) and the meta-audit's root cause.

## Files

| File | Verifies | Result |
|---|---|---|
| [`pip-audit.txt`](pip-audit.txt) | V0.1 — C2 dep CVE claims | **REFUTED** — no vulnerabilities found |
| [`git-history.txt`](git-history.txt) | V0.2, V0.3, V0.4 — fix-of-fix, conventional-commits, large diffs | All VERIFIED |
| [`blocking-sleeps.txt`](blocking-sleeps.txt) | V0.6 — `asyncio.sleep` ≥ 60s sites | UNDERCOUNT — 4 sites, not 1 |
| [`supplemental-spotcheck.md`](supplemental-spotcheck.md) | V0.5 — `python-exhaustive-data.md` accuracy | INFLATED — supplemental numbers 2–4× too high in places |

## Headline corrections to main audit

These were applied to `docs/audits/engineering-constitution-audit.md` (and acknowledged in `docs/audits/meta-audit.md`'s correction table) in the same commit. The meta-audit retains its original narrative about what was unverified at the time of writing — the V0 baseline supersedes those unverified statuses without rewriting the meta-audit's history.

1. **C2** (dep CVEs) — downgraded from CRITICAL #15 finding to MED #5 dependency-management finding. `pip-audit` confirms no CVE exposure under current resolution. Loose minimum-bound pins remain a policy concern (existing installs that never updated could drift), but the audit's "CVEs reachable" framing was wrong.
2. **M3** (5-minute blocking sleep) — undercount. There are **4** `asyncio.sleep ≥ 60s` sites, not 1: `__init__.py:16814`, `optimization/ev_coordinator.py:218,224`, `optimization/coordinator.py:2025`.
3. **H14** (fix-of-fix) — **VERIFIED at 22 commits** with pattern `subject =~ /fix.*fix/i`.
4. **H15** (conventional-commits ratio) — **VERIFIED at 29.1%** (952 of 3,269).
5. **M17** (large-diff Fix commits) — **VERIFIED**. `d20d1b38 Fix API auth for Powerwall control endpoints` is 7,653 lines; `df217bf0 Fix iCloud duplicate files` is 7,637 lines.
6. **M14** (`esy_sunhome` custom not core) — **VERIFIED**. Not on PyPI. `branko-lazarevic/esy_sunhome_modbus` is a HACS-installable custom integration. Listing it in `manifest.json` `after_dependencies` will cause "dependency not found" warnings on installs without it.
7. **Supplemental file (`python-exhaustive-data.md`)** — silent-swallow counts inflated 2–4×. Total broad-except count in supplemental (178) disagrees with direct grep (938). File needs regeneration before reuse.

## What this run did NOT verify

Per constitution #3 (no silent scope reduction):

- Did not re-run scanner agents — V0 verifies the audit, not the scanners' methodology.
- Did not re-verify the `Any` count, return-type count, or `ClientTimeout` count (the previous direct-grep verification was sufficient).
- Did not regenerate `python-exhaustive-data.md` from scratch — flagged as required follow-up.
- Did not run a runtime check (HA test instance) — static analysis only.
- Did not check against a live HA installation. `pip-audit -r reqs.txt` resolves the `>=` floors against PyPI and audits the resolved tree (including transitives once resolved), so transitive coverage is partial: it depends on the resolver picking the same versions as a real install. For maximum fidelity, run `pip-audit` inside the actual HA venv after install.

## Reproducibility

Every command in this V0 run is captured in the four artifact files above. Re-running them on a fresh clone of `bolagnaise/PowerSync` at the same commit should produce identical output (modulo `pip-audit` resolution drift — newer aiohttp releases may shift the resolved version).

## Next step

Phase 1 of the remediation plan (`docs/audits/remediation-plan.md`):
foundation discipline — scaffold CI, lint, typecheck, dependabot, SECURITY.md, CHANGELOG.md, ISSUE_TEMPLATE.

Phase 1 is fork-only and uncontroversial. Land it before Phase 2.
