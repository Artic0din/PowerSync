<!-- release: v2.12.1018 -->

## What's Changed

**Keep Sigenergy zero-export curtailment active during optimizer cleanup**
PowerSync now reasserts the 0 kW export limit when optimizer-owned self-consumption, force-timer, or scheduled-EV no-discharge cleanup restores the inverter's normal EMS mode, ESS limits, and reserve. This prevents uneconomic export while DC curtailment remains active, while manual controls and native/VPP handoff keep their existing behavior.

Update available via HACS
