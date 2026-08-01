<!-- release: v2.12.996 -->

## What's Changed

**Cost Neutral optimizer mode**

Smart Optimization can now cap discretionary battery export at the amount needed to offset the current local day's configured supply charge and measured or forecast import costs. It accounts for measured and forecast natural-solar export credits, then preserves remaining battery energy for self-consumption instead of building unusable account credit.

**Safe limits and clear progress**

Cost Neutral never caps unavoidable solar surplus or relaxes reserve, site/network, Charge By Time, Spread Export, Monitoring Mode, or manual-control safeguards. It is mutually exclusive with Profit Max and exposes its daily target, planned earnings, uncovered amount, supply-charge source, and blocking reason through optimizer status and settings.

Update available via HACS
