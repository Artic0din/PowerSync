<!-- release: v2.12.1313 -->

## Fixes

- Smart Schedule now handles an unavailable current import price without aborting its charging evaluation. Outside an active selected window, Cheapest waits for price data instead of authorizing opportunistic grid charging from a missing price.
- When a Smart Schedule stop cannot be confirmed, its decision now retains the active charging state and reports the pending retry instead of incorrectly reporting that charging stopped.

## Details

- Selected grid and solar windows retain their existing eligibility checks. Solar-first strategies can still use independently eligible surplus, and numeric zero or negative import prices still qualify for free-grid charging.
- Stop handling retains the existing per-vehicle ownership guards, external-controller policy and retry behavior. Reserve and curtailment restoration still follow a successful stop.

Update available via HACS
