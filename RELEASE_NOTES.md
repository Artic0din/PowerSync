<!-- release: v2.12.1036 -->

## What's Changed

**Retry interrupted Sungrow Spread Export cleanup**

PowerSync now keeps an explicit ownership marker when its temporary Sungrow export limit has not been fully restored. A later self-consumption cycle retries the captured pre-export baseline even when the optimizer already reports self-consumption, preventing a failed cleanup from leaving the inverter capped.

**Preserve user settings and Monitoring Mode safety**

Retries restore the exact export-limit baseline captured before Spread Export rather than inferring from incomplete Sungrow telemetry, so an intentional user limit is preserved. Persisted cleanup is deferred while Monitoring Mode is active and resumes only after control is enabled.

Update available via HACS
