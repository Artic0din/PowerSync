<!-- release: v2.12.960 -->

## What's Changed

**SolarEdge Force Charge now selects grid charging**

PowerSync now discovers the SolarEdge Modbus Multi AC Charge Policy control,
temporarily enables grid charging, and selects the explicit Solar-and-Grid
charge command instead of an earlier clipped-solar or solar-only option.
When Force Charge ends, PowerSync restores the site's original AC charge
policy along with the normal SolarEdge storage controls.

Update available via HACS
