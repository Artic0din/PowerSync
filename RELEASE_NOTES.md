<!-- release: v2.12.1310 -->

## What's Changed

### OpenRouter AI Plan Explanation

- Add OpenRouter as an opt-in provider for descriptive Smart Optimization plan explanations.
- Choose an OpenRouter model identifier and save a write-only API key; Gemini and Grok defaults are unchanged.
- Requests use strict structured output and validate every response locally before it is cached or shown.
- OpenRouter and the selected upstream model provider receive the compact verified plan context. Review your model provider's data-processing, cost, quota, and routing settings before enabling it.

Update available via HACS.
