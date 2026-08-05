<!-- release: v2.12.1030 -->

## What's Changed

**Hand capped grid charging straight over to export**
When an optimizer-owned grid charge reaches the configured SOC cap at the same time a fresh plan switches to export, PowerSync now restores the charging mode and issues the export command in the same optimization cycle. This prevents a lower Grid Charge SOC Cap from delaying the start of an eligible export window.

**Keep restore and ownership transitions safe**
The handoff clears stale optimizer charge ownership before restoring the inverter, rolls it back if the restore fails, and blocks re-entrant callbacks from duplicating the export command. Charge-at-cap, reserve, Monitoring Mode, export eligibility, and inverter safety guards remain unchanged.

Update available via HACS
