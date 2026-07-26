<!-- release: v2.12.934 -->

## What's Changed

**Keep CovaU premium export inside the 15c window**
Spread Export now includes CovaU's premium export bonus when identifying price boundaries. Plans no longer flatten battery export from the 15c premium period into later 5c periods, while still spreading the selected energy within the premium window.

**Preserve quota and reserve safeguards**
The optimiser continues to respect CovaU's remaining premium-export quota, the configured software reserve, forecast bridge reserve, battery power limits, and Monitoring Mode. This change only corrects the post-solve placement of already-selected export energy.

Update available via HACS
