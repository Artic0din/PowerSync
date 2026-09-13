<!-- release: v2.12.1286 -->

## What's Changed

**Scheduled Generic Charger charging now reads available EPEX prices correctly**
When a valid current EPEX price is available but the later forecast horizon is shorter, Scheduled Charging now evaluates the current slot instead of treating the entire price schedule as unavailable. Charging still remains safely off when the current slot itself is missing, invalid, stale, or above the configured price limit.

**Local AI plan explanations use bounded forecast context and report truncated answers clearly**
Long action windows now send compact forecast summaries rather than hundreds of raw slots to local OpenAI-compatible providers. Local explanations also receive a larger bounded completion allowance, and a provider that stops for its token limit is reported as an incomplete response without displaying or caching partial text.

Update available via HACS
