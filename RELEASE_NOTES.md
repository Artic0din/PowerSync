<!-- release: v2.12.986 -->

## What's Changed

**Smart Schedule now hands off cleanly between free grid and solar**
Cheapest EV charging now switches an already-running session from free-grid control to live Solar Surplus control when excess solar becomes the selected source. The reverse handoff returns to grid-aware control and restores any temporary curtailment override, while failed handoffs preserve whichever controller is actually still active.

Update available via HACS
