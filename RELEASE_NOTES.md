<!-- release: v2.12.961 -->

## What's Changed

**Settings now live with the feature that owns them**

PowerSync's options are now organised around the system they control. Smart
Optimization contains only its engine, goals, reserve strategy, grid-charging
policy, forecast inputs, and dispatch behaviour. Physical battery controls,
battery specifications, whole-site limits, and EV planning now have dedicated
sections, while the combined first-run setup remains available for quick
installation.

**Hardware reserve is separate from the optimizer reserve**

The battery's outage reserve and restore target now use one canonical setting
under Battery control. Existing Controls values are migrated safely, temporary
optimizer reserve changes cannot replace the user's physical reserve, and the
mobile and Home Assistant settings APIs now report the same restore target.

**Safer shared battery and EV settings**

Battery specification reset now restores safe brand defaults before attempting
auto-detection, site limits remain editable when the native battery optimizer is
selected, and changing EV participation reloads the required charging
coordinators. The settings metadata remains compatible with older clients while
advertising the new ownership model to updated clients.

Update available via HACS
