<!-- release: v2.12.1044 -->

## What's Changed

**Keep electricity-provider changes safe until setup is complete**

Changing providers from Home Assistant now waits for the selected provider's settings to finish before reloading PowerSync. This prevents a Flow Power to Amber change from temporarily activating Amber without a validated token or pricing source.

**Preserve the selected Amber site when updating the API token**

Selecting an active Amber site and replacing the API token in the same Save now keeps that site instead of falling back to the first, potentially closed, account entry. Site IDs are checked against the newly validated token, invalid or stale selections are rejected without changing the saved configuration, and new selections prefer an active site.

Update available via HACS
