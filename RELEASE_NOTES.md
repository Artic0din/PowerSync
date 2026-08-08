<!-- release: v2.12.1048 -->

## What's Changed

**Learn each battery's effective round-trip efficiency**

Smart Optimization can now learn effective AC round-trip efficiency from completed battery charge/discharge loops instead of permanently assuming 92% efficiency in each direction. Learning is provider-neutral, persists across Home Assistant restarts, and remains on the legacy value until it has at least five valid cycles across three local days and 1.5 equivalent full cycles.

The learner rejects incomplete or unsafe evidence, including telemetry gaps, implausible efficiency, inconsistent SOC/power direction, near-empty or near-full SOC, and changed battery topology. Accepted values are bounded, confidence-weighted, and rate-limited so one cycle cannot abruptly change the plan.

**Keep economic decisions conservative**

Learned efficiency improves physical charge, discharge, and SOC modeling, while export and charge-arbitrage decisions remain no more permissive than the existing 84.64% economic hurdle. A higher measured physical efficiency therefore cannot make a marginal 10c import / 11c export cycle appear profitable.

**Add controls and diagnostics**

The new **Learn battery efficiency** advanced optimizer setting controls whether learned values are applied. Turning it off restores legacy optimizer behavior while evidence collection continues. The Battery Power Forecast sensor exposes confidence, valid cycle/day/EFC counts, candidate and applied efficiency, measurement boundary, topology, and rejection reasons in its `efficiency_learning` attribute.

Update available via HACS
