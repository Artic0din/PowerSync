<!-- release: v2.12.1022 -->

## What's Changed

**Recognize grandfathered GloBird ZeroHero tariffs automatically**
PowerSync now distinguishes the pre-July 2026 ZeroHero contract with free charging from 11am-2pm from the newer 12pm-3pm contract by reading the exact Tesla tariff name, rates, and windows. This activates the correct 6pm-9pm Super Export priority and 15 kWh settlement cap for affected systems even when an older saved configuration still says Not on ZeroHero, while preserving any explicit plan or custom settings.

Update available via HACS
