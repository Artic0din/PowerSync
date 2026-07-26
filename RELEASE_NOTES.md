<!-- release: v2.12.936 -->

## What's Changed

**Keep Fronius solar forecasts stable across reloads**
PowerSync now distinguishes unavailable or all-zero Fronius startup telemetry from a genuine zero-solar reading. Smart Optimization no longer treats a transient integration reload snapshot as real solar under-production, preventing the resulting forecast derate from scheduling unnecessary paid grid charging.

**Recover solar nowcasts after transient under-production**
An existing solar derate can now recover when live production returns to within the normal forecast tolerance. This prevents a stale low factor from remaining frozen after valid Fronius telemetry resumes.

**Show the effective grid-charge policy in diagnostics**
Startup and optimization logs now expose whether grid charging is enabled, its effective maximum price, its SOC cap, and how many forecast slots are eligible. Monitoring Mode continues to preserve these saved limits.

Update available via HACS
