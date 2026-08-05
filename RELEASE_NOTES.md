<!-- release: v2.12.1026 -->

## What's Changed

**Let manual Solar Only restart immediately after Stop**
The EV dashboard's Solar Only Start now treats the tap as an explicit manual request, so it no longer inherits the 15-minute automation restart hold created by a prior Stop. Automated Solar Surplus remains held to prevent unwanted restarts, and Limited Grid + Solar and other ownership rules are unchanged.

Update available via HACS
