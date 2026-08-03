<!-- release: v2.12.1012 -->

## What's Changed

**Fixed-rate batteries now stop cleanly at the grid-charge SOC cap**

PowerSync no longer turns a small near-cap optimizer top-up into a full-rate hardware charge command on batteries such as SAJ H2. When the remaining headroom cannot safely fit the fixed command, the plan stays in self-consumption while natural solar charging remains available, preventing repeated Charge/Self-Use mode changes at the end of an import window.

**Normal charging behavior is preserved**

Full fixed-rate charge slots still run when they fit below the configured cap, power-controllable batteries retain partial charge targets, and the default 100% cap behavior is unchanged. A live execution guard also blocks stale fractional plans before they can reach the inverter.

Update available via HACS
