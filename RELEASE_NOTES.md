<!-- release: v2.12.1040 -->

## What's Changed

**Keep planned export power stable through one-slot gaps**

During a planned export, a fresh five-minute solve could smooth a one-slot self-consumption gap into an export action at the battery's much lower natural-discharge power, replacing an already active high-power command on target-power batteries such as Sungrow. PowerSync now carries that bridged-slot context through schedule reconciliation and reuses the existing validated export commitment instead of reducing the hardware target.

**Keep every export safety gate authoritative**

The commitment still releases immediately when its window or eligibility closes, the export price falls below the minimum, EV preservation, calibration, or demand rules intervene, projected SOC would cross reserve, site or network limits change, or manual or external ownership applies.

Update available via HACS
