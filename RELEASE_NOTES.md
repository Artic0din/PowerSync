<!-- release: v2.12.1055 -->

## What's Changed

**Keep profitable battery export when future load needs only part of the stored energy**

PowerSync no longer cancels an entire generic battery-export plan merely because some later household load has a higher import price. The optimizer now reserves the forecast energy needed for that future load and can export only the already-stored surplus above the reserve, so ordinary Amber and other dynamic-price forecast refreshes do not turn a partly profitable export opportunity into an all-or-nothing decision.

**Preserve export safety through the complete schedule path**

The same energy reservation now applies to the HiGHS optimizer, greedy fallback, schedule spreading, reconciliation, and short-gap bridging. Grid charging cannot replenish a protected generic-export budget, while acquisition-cost gates, Priority Export, Cost Neutral, direct solar export, configured reserves, and normal household self-consumption retain their existing behavior. Optimization diagnostics now report whether future-load reservation or acquisition cost constrained battery export and show the reserved and planned energy.

Update available via HACS
