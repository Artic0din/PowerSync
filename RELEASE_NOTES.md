<!-- release: v2.12.930 -->

## What's Changed

**Keep GoodWe No Idle holds from changing backup reserve**
When Charge By Time needs a short deadline-critical hold, GoodWe systems now use EMS `conserve` without rewriting the inverter's persistent DOD / backup reserve to the current SOC. PowerSync still restores EMS Auto / General operation when the hold ends.

**Safe retry when conserve control is unavailable**
If the GoodWe EMS hold command is unavailable or rejected, PowerSync leaves the optimizer action pending for retry instead of falling back to a DOD write.

Update available via HACS
