<!-- release: v2.12.979 -->

## What's Changed

**Correct EPEX setup and pricing guidance**
PowerSync's EPEX setup text now matches the integration's actual pricing contract: Belgium uses native 15-minute day-ahead intervals, the other supported regions remain hourly aggregated, and an unconfigured fixed export rate safely values exports at 0 ct/kWh unless an export price entity supplies values. The supported-zone examples now list only regions PowerSync can configure.

Update available via HACS
