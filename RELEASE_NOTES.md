<!-- release: v2.12.952 -->

## What's Changed

**CovaU SolarMax tariff windows now follow your Home Assistant timezone**
Advertised clock times such as the `11:00-14:00` free-import window now remain at those local times in South Australia, New South Wales, Queensland, and other configured timezones. This removes the 30-minute South Australian offset and follows daylight-saving changes instead of forcing every CovaU plan onto a fixed AEST clock.

**Existing CovaU installations migrate automatically**
Saved CovaU plan snapshots adopt the configured local timezone on reload. PowerSync safely starts a fresh measured quota baseline when the tariff clock changes so previously settled usage is not carried across incompatible day boundaries.

Update available via HACS
