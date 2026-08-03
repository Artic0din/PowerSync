<!-- release: v2.12.1010 -->

## What's Changed

**Mixed solar and grid battery energy is valued proportionally**

PowerSync now values each supported part of the battery's current inventory separately: measured grid-charged energy keeps its actual cost, proven solar-charged energy remains free, and unknown carry-over stays conservatively valued from import prices. A very small grid top-up can no longer price the entire mostly solar-filled battery at that one sample's rate.

**Export plans retain their profitability safeguards**

Flow Power Happy Hour and other permitted export windows can now use the correct blended stored-energy cost when deciding whether to export. PowerSync still blocks intentional battery export below the calculated acquisition cost and preserves conservative behavior when energy provenance is unavailable.

Update available via HACS
