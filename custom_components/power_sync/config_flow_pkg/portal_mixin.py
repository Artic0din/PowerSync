"""Reusable portal / MFA login helpers for Flow Power and GloBird.

Phase 6 extracts credential validation and documents the mixin surface that
setup/options can inherit once portal steps are split out of the large flow
classes. Full step methods remain on PowerSyncConfigFlow / PowerSyncOptionsFlow
for now (strangler).
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


async def validate_globird_portal_credentials(email: str, password: str) -> str | None:
    """Validate GloBird portal credentials; return an error key or None.

    Thin wrapper around helpers._validate_globird_credentials so portal steps
    can depend on this module without importing the full helpers surface.
    """
    from .helpers import _validate_globird_credentials

    return await _validate_globird_credentials(email, password)


async def authenticate_flow_power_portal(
    email: str, password: str
) -> tuple[Any | None, dict[str, Any] | None, str | None]:
    """Authenticate with Flow Power portal.

    Returns ``(client, auth_result, error_key)``.
    On MFA, ``auth_result["status"] == "mfa_required"`` and client is live.
    On failure, client is closed/None and error_key is set.
    """
    from ..flow_power_portal import FlowPowerPortalClient

    client = FlowPowerPortalClient()
    try:
        result = await client.authenticate(email, password)
        return client, result, None
    except ValueError:
        await client.close()
        return None, None, "invalid_credentials"
    except Exception as err:
        _LOGGER.exception("Flow Power portal login failed: %s", err)
        await client.close()
        return None, None, "cannot_connect"


class PortalAuthMixin:
    """Mixin documenting shared portal/MFA helpers for config and options flows.

    Intended inheritance (next phase)::

        class PowerSyncConfigFlow(..., PortalAuthMixin):
            ...

    Methods below are safe to call from either flow once ``self.hass`` and
    provider-specific state attrs exist. They do not replace the existing
    ``async_step_*_portal*`` methods yet.
    """

    # Flow Power — attrs expected on the host flow:
    #   _fp_client, _fp_email, _fp_password, _flow_power_data

    async def portal_authenticate_flow_power(
        self, email: str, password: str
    ) -> str | None:
        """Authenticate Flow Power; returns error key or None if MFA/success path set."""
        client, result, error = await authenticate_flow_power_portal(email, password)
        if error:
            self._fp_client = None
            return error
        self._fp_client = client
        if result and result.get("status") == "mfa_required":
            self._fp_email = email
            self._fp_password = password
            return "mfa_required"
        return "cannot_connect"

    async def portal_verify_flow_power_mfa(self, code: str) -> bool:
        """Verify Flow Power MFA code using ``self._fp_client``."""
        client = getattr(self, "_fp_client", None)
        if not client or not code:
            return False
        return bool(await client.verify_mfa(code))

    async def portal_validate_globird(self, email: str, password: str) -> str | None:
        """Validate GloBird credentials; return error key or None on success."""
        return await validate_globird_portal_credentials(email, password)
