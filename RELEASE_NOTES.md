<!-- release: v2.12.963 -->

## What's Changed

**Smart Optimization settings are grouped in one place**

PowerSync no longer splits optimizer controls into separate Battery Control,
Battery Specifications, Home & Grid Limits, or EV settings destinations. The
Home Assistant options flow now keeps Core Goals, Reserve Controls, Battery &
Forecast Inputs, Grid & Site Constraints, and Dispatch Behaviour together in
one Smart Optimization form.

**Hardware Reserve and Monitoring Mode remain optimizer controls**

Hardware Reserve now sits beside the optimizer and Auto-Apply reserve controls,
while Monitoring Mode remains with dispatch behaviour. Saving the grouped form
continues to preserve cleared optional values and live settings changed by
another client while the form was open.

**Mobile settings match Home Assistant**

The mobile app uses the same grouped layout, removes the duplicate editors from
Battery Setup, EV Charging, Home Power, and the optimization status screen, and
normalizes the previous split settings metadata during staggered app and HACS
upgrades.

Update available via HACS
