<!-- release: v2.12.959 -->

## What's Changed

**CovaU free-import windows now keep Force Charge active**

PowerSync now treats CovaU's quota-backed import credit as a free charging
window when mapping optimizer actions, not only when calculating settlement
costs. Charge By Time remains in Force Charge for the full quota-covered
window even after the battery reaches its grid-charge target, while per-day
quota limits still prevent paid intervals from inheriting the command.

Update available via HACS
