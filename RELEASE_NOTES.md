<!-- release: v2.12.1307 -->

## What's Changed

**Smart Schedule keeps permitted deadline charging on track when live solar drops**
When a Meet Deadline plan had reserved forecast solar but that surplus disappeared at runtime, PowerSync could wait instead of using permitted grid energy. Time-critical schedules now switch to a clearly reported grid deadline fallback while preserving no-grid-import and demand-window blocks. This prevents a forecast-only solar window from silently missing a configured deadline.

Update available via HACS
