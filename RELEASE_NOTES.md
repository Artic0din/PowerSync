<!-- release: v2.12.994 -->

## What's Changed

**AI explanations now explain optimizer decisions**
Gemini and Grok now receive the same versioned, provider-neutral PowerSync explainer contract and a compact set of verified plan facts. Explanations lead with what is happening now, what changes next and when, why the plan is better than the relevant supplied alternative, the expected outcome, and what may change, instead of reciting every schedule interval.

**Verified feedback with the same descriptive-only safety boundary**
PowerSync can compare the current plan with the last successfully explained plan and supply verified tariff, forecast, battery-level, EV-plan, warning, and input-availability changes without asking the model to calculate or invent a cause. AI remains unable to control, execute, modify, or recommend changes to the optimizer or connected hardware, and the last valid explanation remains available when a provider response is unavailable or malformed.

**Clearer explanations across the dashboard and mobile app**
The Home Assistant card now renders the new Right now, Next, Why this plan, Expected outcome, optional timeline, and What may change sections, including local currency and natural units. Existing mobile app builds receive compatible now/next/outcome text immediately, while the updated mobile source adds the same sectioned layout for future store builds.

**Responsive Month and Year energy history**
Non-Tesla calendar history now uses the same shielded background task, timeout, and short-term cache behavior as Tesla history. Month and Year requests no longer fall back to expensive raw recorder scans when Home Assistant long-term statistics are unavailable, and the response clearly identifies limited history instead of leaving the mobile request hanging.

Update available via HACS
