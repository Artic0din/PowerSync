<!-- release: v2.12.931 -->

## What's Changed

**Preserve Tesla Grid Charging across force modes**
Tesla force charge and force discharge now capture the site's Grid Charging preference before changing Powerwall settings and restore only a confirmed local, cloud, or previously remembered value. Charge-to-discharge transitions preserve the original preference instead of treating the intermediate force-mode state as the baseline.

**Reject non-restorable Tesla force sessions safely**
When Tesla does not expose the current Grid Charging value and PowerSync has no remembered preference, the force command now stops before sending any Powerwall write instead of guessing and persisting Grid Charging as disabled. Set the PowerSync Grid Charging control once to establish the preference, then retry. If an earlier session already left Grid Charging off, re-enable it once after updating.

Update available via HACS
