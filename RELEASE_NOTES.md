<!-- release: v2.12.1049 -->

## What's Changed

**Stop Tesla grid charging before demand windows**

PowerSync now pre-arms Tesla grid-charging protection one minute before the configured demand-charge start and checks on exact minute boundaries. This prevents Tesla API confirmation time from carrying an optimizer or force-charge command into the billed period. Demand sensors, peak tracking, and cost calculations still use the exact configured start and end times.

Tesla force-charge expiry is now anchored when the command begins, so site and tariff setup time cannot extend the requested charge duration. Slow setup, delayed Powerwall charge-kick, restore, and TOU-sync paths all recheck demand protection and cannot re-enable grid charging after the guard activates.

Update available via HACS
