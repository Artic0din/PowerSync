<!-- release: v2.12.967 -->

## What's Changed

**SAJ H2 optimizer charging now holds one continuous command**
PowerSync no longer rewrites the SAJ H2 Time-of-Use charge slot at every cached and freshly solved optimizer refresh. Optimizer-owned charge and discharge commands now use the direct hardware path, retain their private lifecycle, recognize an active SAJ TOU mode even when battery power naturally varies, and retry a failed refresh without recording it as successful. This removes the repeated control writes behind the reported 2.5–5 kW charging oscillation while preserving recovery if the inverter drops out of TOU mode.

Update available via HACS
