<!-- release: v2.12.993 -->

## What's Changed

**AI plan explanations in Home Assistant**
The generated PowerSync dashboard now includes an optional AI Plan Explanation card alongside the deterministic action plan. Explanations are generated only when you select Generate or Refresh, use the existing authenticated backend, safely display cached and stale states, and cannot change or execute the optimiser plan.

**Gemini and Grok setup in Configure**
Gemini or Grok can now be selected and its write-only API key managed from Settings > Devices & Services > PowerSync > Configure > Smart Optimization. The grouped Smart Optimization form also restores the descriptive guidance beneath every setting.

**Optional EV plans no longer reported as failed inputs**
AI explanations now treat configured EV integration with no active charging plan as a valid empty state instead of reporting that the `ev_plan` input is unavailable.

Update available via HACS
