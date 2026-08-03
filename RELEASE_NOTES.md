<!-- release: v2.12.1014 -->

## What's Changed

**Tesla EV charging now uses the healthy control provider when integrations overlap**
When the same Tesla is exposed by more than one Home Assistant integration, PowerSync now selects the provider with usable charging controls instead of relying on device-registry order. This prevents an old or re-authentication-needed Tesla Fleet device from masking a healthy Teslemetry device for wake, start, stop, charge-limit, current, status, and charging-power operations. Home Assistant's numbered duplicate entity IDs, such as `_2`, are now supported consistently across those paths.

Update available via HACS
