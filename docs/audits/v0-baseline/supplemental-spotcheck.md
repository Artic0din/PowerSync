# Supplemental file spot-check — V0.5

**Date:** 2026-05-27
**Subject:** `docs/audits/python-exhaustive-data.md` (claimed "exhaustive")
**Method:** Pick 5 specific numeric claims; re-verify each by direct grep / AST scan.

## Results

Method — true AST walk (regex on source text undercounts because it does
not match `pass # comment` variants; Codex feedback on this PR pointed
that out, and the AST count is now the authoritative method):

```python
import ast, pathlib
counts = {}
for p in pathlib.Path('custom_components/power_sync').rglob('*.py'):
    try: tree = ast.parse(p.read_text())
    except SyntaxError: continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for h in node.handlers:
                # bare except OR except Exception
                if h.type is None or (isinstance(h.type, ast.Name) and h.type.id == 'Exception'):
                    if len(h.body) == 1 and isinstance(h.body[0], ast.Pass):
                        counts[str(p)] = counts.get(str(p), 0) + 1
print(counts)
```

| Claim in supplemental | AST verified | Verdict |
|---|---|---|
| `__init__.py` silent count = **82** | **23** | REFUTED — supplemental inflated ~3.6× |
| `automations/actions.py` silent count = **22** | **12** | REFUTED — supplemental inflated ~1.8× |
| Total silent across tree (supplemental implied 178 broad / 76 silent) | **84 silent / 942 broad+bare** | REFUTED on broad (5×); silent within ~10% but AST > regex |
| `async_register` calls in `__init__.py` = **30** (with 0 schema) | **30** (`grep -c "async_register(" __init__.py`) | VERIFIED |
| `requires_auth = True` count — supplemental said "9 of 74 missing" | **76 matches across 75 view classes** (`grep -rn "requires_auth\s*=\s*True" custom_components/`) | **REFUTED** — all views have `requires_auth = True` |
| `HomeAssistantView` subclass count | **75** (`grep -rn "class .*HomeAssistantView" custom_components/`) | Close to supplemental's 74 (off by 1) |

## Interpretation

The supplemental file (`python-exhaustive-data.md`, 551 lines, claimed
"exhaustive enumeration") inflated silent-swallow counts by roughly 2–4×.

The total broad `except Exception` count of **940** (verified earlier by
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
