# Supplemental file spot-check — V0.5

**Date:** 2026-05-27
**Subject:** `docs/audits/python-exhaustive-data.md` (claimed "exhaustive")
**Method:** Pick 5 specific numeric claims; re-verify each by direct grep / AST scan.

## Results

Method (Python `re.findall`, alternation expressed with real `|` —
table-cell pipe is escaped as `&#124;` for rendering):

```python
import re
src = open('custom_components/power_sync/__init__.py').read()
matches = re.findall(r'except\s+Exception[^:]*:\s*\n\s+pass\s*(?:\n|$)', src)
print(len(matches))
```

| Claim in supplemental | Verified result | Verdict |
|---|---|---|
| `__init__.py` silent `except Exception: pass` count = **82** | **21** | **REFUTED** — supplemental inflated by ~4× |
| `automations/actions.py` silent swallows = **22** | **11** | **REFUTED** — inflated by ~2× |
| `async_register` calls in `__init__.py` = **30** (with 0 schema) | **30** (`grep -c "async_register(" __init__.py`) | VERIFIED |
| `requires_auth = True` count — supplemental said "9 of 74 missing" | **76 matches across 75 view classes** (`grep -rn "requires_auth\s*=\s*True" custom_components/`) | **REFUTED** — all views have `requires_auth = True` |
| `HomeAssistantView` subclass count | **75** (`grep -rn "class .*HomeAssistantView" custom_components/`) | Close to supplemental's 74 (off by 1) |

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
