# Supplemental file spot-check — V0.5

**Date:** 2026-05-27
**Subject:** `docs/audits/python-exhaustive-data.md` (claimed "exhaustive")
**Method:** Pick 5 specific numeric claims; re-verify each by direct grep / AST scan.

## Results

| Claim in supplemental | Method | Verified result | Verdict |
|---|---|---|---|
| `__init__.py` silent `except Exception: pass` count = **82** | `re.findall(r'except\s+Exception[^:]*:\s*\n\s+pass\s*(?:\n\|$)', src)` | **21** | **REFUTED** — supplemental inflated by ~4× |
| `automations/actions.py` silent swallows = **22** | same | **11** | **REFUTED** — inflated by ~2× |
| `async_register` calls in `__init__.py` = **30** (with 0 schema, "30 of 30") | `grep -c "async_register(" __init__.py` | **30** | VERIFIED |
| `requires_auth = True` grep count = supplemental said "9 missing of 74" | `grep -rn "requires_auth\s*=\s*True" custom_components/` | **76 matches** across **75 view classes** | **REFUTED** — all views have `requires_auth = True`, not 9 missing |
| `HomeAssistantView` subclass count | `grep -rn "class .*HomeAssistantView" custom_components/` | **75** | Close to supplemental's 74 (off by 1) |

## Interpretation

The supplemental file (`python-exhaustive-data.md`, 551 lines, claimed
"exhaustive enumeration") inflated silent-swallow counts by roughly 2–4×.

The total broad `except Exception` count of **938** (verified earlier by
direct grep) is also at odds with the supplemental's claim of **178**.

This means the meta-audit's verification step was itself incomplete: the
supplemental was treated as ground truth without verification, and it
turned out to be wrong in the same direction as the original scanner —
just less wrong.

**Pattern:** every layer of trust without verification compounds error.
The plan's Phase 1 verification gate (Phase 7.1 `verify-audit.sh`) is the
only durable fix.

## Action required

1. Regenerate `python-exhaustive-data.md` from scratch using direct
   greps, not scanner output.
2. Re-verify every numeric claim it makes.
3. Until regenerated, treat all of its numbers as approximate ±50%.
