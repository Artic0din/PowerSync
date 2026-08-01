<!-- release: v2.12.992 -->

## What's Changed

**Optional AI explanations for Smart Optimization plans**
PowerSync can now explain the existing deterministic 24-hour action plan using a user-supplied Gemini or Grok API key. Explanations are generated only after an explicit request from the mobile app, use a compact plan snapshot, and are cached in memory while that plan remains current.

**Write-only credentials and descriptive-only output**
Provider keys are stored in the Home Assistant config entry and are never returned by the API. PowerSync validates structured provider responses, rejects unknown action-window references, discards responses when the plan changes during generation, and keeps the AI path isolated from optimizer settings, Home Assistant services, and battery or inverter commands.

Update available via HACS
