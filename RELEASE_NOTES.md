<!-- release: v2.12.938 -->

## What's Changed

**Configure Smart Schedule departure times from the Home Assistant dashboard**
Each Smart Schedule vehicle now has editable Monday-to-Sunday departure times in the native PowerSync dashboard. Individual days can be changed, saved, left blank, or cleared without opening the mobile app.

**Remove phantom legacy vehicle rows**
PowerSync now removes superseded numeric and default Smart Schedule records once a stable vehicle ID is available. This prevents stale entries such as “Vehicle 1” from appearing alongside the real vehicle, while retaining backward compatibility for installations that still only have a legacy vehicle record.

Update available via HACS
