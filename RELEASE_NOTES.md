<!-- release: v2.12.942 -->

## What's Changed

**Tesla self-consumption automations no longer false-fail on omitted readback**
When Tesla accepts a Set Operation Mode automation but repeatedly omits `default_real_mode` from otherwise valid `site_info` responses, PowerSync now treats `self_consumption` as compatibility-confirmed instead of retrying the command and sending a false failure notification. Explicitly conflicting modes, invalid or incomplete responses, and the stricter Autonomous and Backup paths still fail verification normally.

Update available via HACS
