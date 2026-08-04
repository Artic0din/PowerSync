<!-- release: v2.12.1024 -->

## What's Changed

**Add account-aware GloBird FOUR4FREE tariff modes**
PowerSync now recognizes FOUR4FREE automatically from an authoritative Tesla or imported tariff schedule without applying ZeroHero caps, credits, or settlement rules. This keeps each account's actual import and export prices as the source for price sensors and Smart Optimization instead of guessing rates from another FOUR4FREE offer.

**Support durable manual FOUR4FREE tariffs**
GloBird setup and options now include a separate manual/custom FOUR4FREE choice that opens PowerSync's tariff editor for the exact periods and rates in the account Welcome Pack. A selected manual tariff remains authoritative after Home Assistant restarts, including on Tesla systems, while automatic mode continues to use the tariff stored on the Powerwall.

Update available via HACS
