<!-- release: v2.12.947 -->

## What's Changed

**Sigenergy export now stays at its grid target when home load changes**
PowerSync now keeps the full rated or configured ESS discharge headroom available during optimizer export while the separate grid-point limit enforces the requested export ceiling. A 5 kW export target therefore remains 5 kW as household consumption rises, instead of sharing a fixed battery-output cap with the home.

Update available via HACS
