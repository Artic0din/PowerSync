<!-- release: v2.12.1035 -->

## What's Changed

**Cost Neutral zero-cap safety correction**

Daily Cost Neutral budgets with exactly zero available earnings now stay on the bounded planning path. This prevents a zero-cap day from using discretionary planned grid imports to create a new export allowance, while later local days with a positive budget still account for their own required imports independently.

This is a follow-up safety correction to the provider-neutral multi-day Cost Neutral planning introduced in v2.12.1034.

Update available via HACS
