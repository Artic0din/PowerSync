<!-- release: v2.12.956 -->

## What's Changed

**Multiple Smart Schedule vehicles now share the site import limit**

When two Teslas charge at the same time, PowerSync now allocates the available
site headroom once across both vehicles instead of giving each vehicle the full
allowance. This prevents paired vehicles from repeatedly starting together,
exceeding the configured site limit, and stopping together, while still allowing
both to charge when the connection can sustain both minimum rates.

Update available via HACS
