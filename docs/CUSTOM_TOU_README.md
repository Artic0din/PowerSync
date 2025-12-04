# Custom TOU Scheduler

Create fixed-rate electricity schedules for any Australian provider - not just Amber Electric.

## Overview

The Custom TOU (Time-of-Use) Scheduler allows you to create and upload static tariff schedules to your Tesla Powerwall. This is useful if you:

- Use a provider other than Amber Electric
- Have a complex tariff with more than 4 rate tiers
- Want to manually control your TOU schedule
- Need demand charge tracking

## Key Features

- **Unlimited time periods** - Not limited to Tesla's 4-tier restriction
- **30-minute granularity** - Aligned with Australian NEM intervals
- **Multiple seasons** - Support summer/winter rate variations
- **Demand charges** - Capacity-based fees ($/kW)
- **Day-of-week control** - Different rates for weekdays/weekends
- **Tesla API validation** - Ensures compliance with Tesla's requirements

## Getting Started

### 1. Navigate to Custom TOU

Go to `/custom-tou` in the Flask web app (or click "Custom TOU" in the navigation).

### 2. Create a Schedule

Click "Create Schedule" and enter:

| Field | Description | Example |
|-------|-------------|---------|
| **Utility Provider** | Your electricity company | "Origin Energy" |
| **Rate Plan Name** | Descriptive plan name | "Single Rate + TOU" |
| **Tariff Code** | Official code from your bill (optional) | "EA205" |
| **Daily Charge** | Daily supply charge in $ | 1.177 |

### 3. Add a Season

Each schedule needs at least one season. For year-round rates, create an "All Year" season (1/1 to 12/31).

For seasonal pricing:
- **Summer**: e.g., 12/1 to 2/28
- **Winter**: e.g., 6/1 to 8/31

### 4. Add Time Periods

For each season, add your rate periods:

| Field | Description |
|-------|-------------|
| **Name** | Period name (e.g., "Peak", "Off-Peak") |
| **Time Range** | Start and end times (30-min aligned) |
| **Day Range** | Days of week this applies |
| **Buy Rate** | What you pay to import ($/kWh) |
| **Sell Rate** | What you get for export ($/kWh) |
| **Demand Rate** | Capacity charge ($/kW, optional) |

### 5. Preview & Sync

1. Click "Preview" to see the generated Tesla tariff
2. Verify the rates look correct
3. Click "Sync to Tesla" to upload

## Example: Origin Energy TOU

**Schedule:**
- Utility: "Origin Energy"
- Name: "Single Rate + TOU"
- Daily Charge: $1.177

**Season:** All Year (1/1 - 12/31)

**Periods:**

| Name | Time | Days | Buy | Sell |
|------|------|------|-----|------|
| Peak | 14:00-20:00 | Mon-Fri | $0.35 | $0.05 |
| Shoulder | 07:00-14:00 | Mon-Fri | $0.25 | $0.05 |
| Off-Peak | 20:00-07:00 | Mon-Fri | $0.15 | $0.05 |
| Weekend | 00:00-00:00 | Sat-Sun | $0.20 | $0.05 |

## Tesla API Restrictions

The system automatically validates:

- **No negative prices** - Clamped to $0 minimum
- **Buy ≥ Sell** - Buy rate must be >= sell rate
- **30-minute alignment** - All times aligned to :00 or :30

## Common Australian TOU Patterns

### Simple 3-Tier
- Peak: 14:00-20:00 weekdays
- Shoulder: 07:00-14:00, 20:00-22:00 weekdays
- Off-Peak: All other times

### Demand + TOU
- Standard 3-tier energy rates
- Plus demand charge during peak periods ($/kW for maximum draw)

### Seasonal Rates
- Different rates for summer (higher peak) vs winter
- Create separate seasons with different periods

## How It Appears in Tesla App

```
┌─────────────────────────────────────┐
│ Utility Rate Plan                   │
├─────────────────────────────────────┤
│ Origin Energy                       │ ← Utility Provider
│ Single Rate + TOU (EA205)           │ ← Rate Plan Name (Code)
│                                     │
│ Daily Supply: $1.18                 │
│ Time-of-Use Rates: 4 periods        │
└─────────────────────────────────────┘
```

## Notes

- Custom TOU schedules are **one-time uploads** - they don't auto-update like Amber sync
- You can have multiple saved schedules and switch between them
- Changes won't affect Amber auto-sync if that's also enabled (they use separate tariff sources)

## Support

- Tesla TOU API docs: https://developer.tesla.com/docs/fleet-api/endpoints/energy
- Example tariff format: https://digitalassets-energy.tesla.com/raw/upload/app/fleet-api/example-tariff/PGE-EV2-A.json
