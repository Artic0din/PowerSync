<!-- release: v2.12.950 -->

## What's Changed

**Multi-Tesla BLE automations now target the vehicle that is actually charging**
EV charging triggers now inspect every matching Tesla state instead of accepting the first entity returned by Home Assistant. An actively charging BLE vehicle wins over sleeping or idle vehicles, and a targetable BLE identity wins over a duplicate Fleet state when both report charging, so stop actions reach the correct car.

**Smart Schedule keeps each vehicle's state isolated**
An explicit Fleet vehicle schedule no longer borrows battery state of charge from an unrelated configured BLE vehicle. This prevents multi-car installations from building one vehicle's charging plan with another vehicle's live battery level when provider identities coexist.

Update available via HACS
