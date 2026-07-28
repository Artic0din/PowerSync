<!-- release: v2.12.962 -->

## What's Changed

**Capped free-import charging now respects fixed-rate battery controls**

PowerSync now prevents a partial tariff allowance from becoming an
unbounded full-rate Force Charge on batteries such as SAJ H2 that cannot
honor a requested charge power. Fully quota-covered free windows still keep
Force Charge active, batteries with controllable charge power can still use
partial allowances, and configured site-import limits remain protected.

Update available via HACS
