<!-- release: v2.12.1315 -->

## What's Changed

**Configure your account-specific Flow Power export tiers**
Select Account-specific in Flow Power settings and enable Configure custom
export tiers. Enter the premium export rate, daily premium allowance, rate after
that allowance, rate outside the export window, and effective date. Setup and
options support one daily window in 30-minute steps, including midnight as the
end time. Existing flat-rate accounts retain their settings unless custom tiers
are explicitly enabled.

**Use measured remaining allowance in optimization and earnings**
The optimizer values the remaining premium allowance separately from the
post-allowance rate. Only measured exports within the configured window consume
the allowance; forecasts do not. The allowance resets at local midnight, and
unknown quota confidence withholds the premium until a usable baseline is
established. Export earnings now account for tariff-window boundaries rather
than pricing the whole interval at its final rate.

**Keep the contract consistent across settings, sensors and battery tariffs**
Unrelated settings saves preserve the selected contract and quota state, while
changed contract terms clear incompatible pending settlement and ledger state.
Export-price attributes expose the contract and active window. Static tariffs
sent to batteries use post-allowance and outside-window rates because those
tariffs cannot encode a cumulative daily premium. Invalid dates, rates,
allowances and windows are rejected before settings are applied.

Update available via HACS
