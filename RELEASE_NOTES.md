<!-- release: v2.12.1034 -->

## What's Changed

**Cost Neutral now plans every local day in the optimization horizon**

Tomorrow's export windows are planned independently when today's electricity costs are already covered. Daily budgets use Home Assistant local dates, measured costs only for the current day, forecast costs for future days, and the correct monthly supply charge across month boundaries.

**Provider-neutral tariff and optimizer support**

The multi-day contract is shared by every electricity provider, including quota tariffs such as CovaU/ZeroHero and synthetic windows such as FOUR4FREE. Billable settlement rates remain separate from optimizer boosts, quota bonuses, saving sessions, demand penalties, and confidence overlays.

**Safety constraints and diagnostics retained**

Battery reserve, export permissions, site/network limits, natural solar export, and zero-cap anti-bootstrap safeguards remain enforced per day. Cost Neutral status now exposes per-day caps and planned earnings while preserving the existing current-day fields for compatibility, with DST-safe interval timelines.

Update available via HACS
