<!-- release: v2.12.1039 -->

## What's Changed

**Keep tomorrow's reserve forecast from blocking today's export**

Auto-Apply Optimizer Reserve now raises its live software floor only from export episodes on the current local calendar day. A larger forecast bridge for tomorrow can no longer lift today's reserve to 100% and suppress an eligible Flow Power Happy Hour or other planned export.

**Recalculate safely at the local-day boundary**

Future-day export episodes are reconsidered when that local day begins. PowerSync compares aware timestamps in the Home Assistant local timezone and ignores an episode when its calendar date cannot be established safely.

Update available via HACS
