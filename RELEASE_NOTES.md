<!-- release: v2.12.1045 -->

## What's Changed

**Make automation power thresholds use the displayed kW values**

Flow triggers and IF conditions now compare solar, home usage, grid import/export, and battery charge/discharge against the real coordinator values in kW. This fixes cases where a 10 kW grid import was treated as 0.01 kW, causing a condition such as **Grid Import below 5 kW** to pass incorrectly.

Update available via HACS
