<!-- release: v2.12.1008 -->

## What's Changed

**Cost Neutral is now configurable in Smart Optimization**

Cost Neutral and its fixed Daily Supply Charge now sit together under Core Goals. The daily charge is treated as part of the amount Cost Neutral must recover, rather than as a demand charge, and changes made in Home Assistant or the mobile API trigger a fresh optimization immediately.

**Existing monthly supply-charge configurations remain compatible**

PowerSync continues to convert a legacy monthly supply charge into a daily amount when no positive daily charge is configured. Invalid or negative daily values are rejected before settings are saved.

**Mobile clients can discover and manage both settings**

The optimizer settings API now advertises and returns Cost Neutral and Daily Supply Charge with their correct ownership and placement, while keeping Cost Neutral mutually exclusive with Profit Maximisation.

Update available via HACS
