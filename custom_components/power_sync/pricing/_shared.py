"""Shared helpers for pricing and energy coordinators."""
from __future__ import annotations

from datetime import datetime, timedelta, date
import logging
import re
import time
from typing import Any, Optional
import asyncio

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from ..const import (
    DOMAIN,
    POWER_SYNC_USER_AGENT,
)
from ..sensitive_logging import obfuscate_log_arg, obfuscate_vin_tokens


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that obfuscates sensitive data like API keys and tokens.
    Shows first 4 and last 4 characters with asterisks in between.
    """

    @staticmethod
    def obfuscate(value: str, show_chars: int = 4) -> str:
        """Obfuscate a string showing only first and last N characters."""
        if len(value) <= show_chars * 2:
            return '*' * len(value)
        return f"{value[:show_chars]}{'*' * (len(value) - show_chars * 2)}{value[-show_chars:]}"

    def _obfuscate_string(self, text: str) -> str:
        """Apply all obfuscation patterns to a string."""
        if not text:
            return text

        # Handle Bearer tokens
        text = re.sub(
            r'(Bearer\s+)([a-zA-Z0-9_-]{20,})',
            lambda m: m.group(1) + self.obfuscate(m.group(2)),
            text,
            flags=re.IGNORECASE
        )

        # Handle psk_ tokens (Amber API keys)
        text = re.sub(
            r'(psk_)([a-zA-Z0-9]{20,})',
            lambda m: m.group(1) + self.obfuscate(m.group(2)),
            text,
            flags=re.IGNORECASE
        )

        # Handle authorization headers in websocket/API logs
        text = re.sub(
            r'(authorization:\s*Bearer\s+)([a-zA-Z0-9_-]{20,})',
            lambda m: m.group(1) + self.obfuscate(m.group(2)),
            text,
            flags=re.IGNORECASE
        )

        # Handle site IDs (alphanumeric, like Amber 01KAR0YMB7JQDVZ10SN1SGA0CV)
        text = re.sub(
            r'(site[_\s]?[iI][dD]["\']?[\s:=]+["\']?)([a-zA-Z0-9-]{15,})',
            lambda m: m.group(1) + self.obfuscate(m.group(2)),
            text
        )

        # Handle "for site {id}" pattern
        text = re.sub(
            r'(for site\s+)([a-zA-Z0-9-]{15,})',
            lambda m: m.group(1) + self.obfuscate(m.group(2)),
            text,
            flags=re.IGNORECASE
        )

        # Handle email addresses
        text = re.sub(
            r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
            lambda m: self.obfuscate(m.group(1)),
            text
        )

        # Handle Tesla energy site IDs (numeric, 13-20 digits) - in URLs and JSON
        text = re.sub(
            r'(energy_site[s]?[/\s:=]+["\']?)(\d{13,})',
            lambda m: m.group(1) + self.obfuscate(m.group(2)),
            text,
            flags=re.IGNORECASE
        )

        # Handle standalone long numeric IDs (Tesla energy site IDs in various contexts)
        text = re.sub(
            r'(\bsite\s+)(\d{13,})',
            lambda m: m.group(1) + self.obfuscate(m.group(2)),
            text,
            flags=re.IGNORECASE
        )

        # Handle VIN numbers in JSON format ('vin': 'XXX' or "vin": "XXX")
        text = re.sub(
            r'(["\']vin["\']:\s*["\'])([A-HJ-NPR-Z0-9]{17})(["\'])',
            lambda m: m.group(1) + self.obfuscate(m.group(2)) + m.group(3),
            text,
            flags=re.IGNORECASE
        )

        # Handle VIN numbers plain format
        text = re.sub(
            r'(\bvin[\s:=]+)([A-HJ-NPR-Z0-9]{17})\b',
            lambda m: m.group(1) + self.obfuscate(m.group(2)),
            text,
            flags=re.IGNORECASE
        )
        text = obfuscate_vin_tokens(text, self.obfuscate)

        # Handle DIN numbers in JSON format
        text = re.sub(
            r'(["\']din["\']:\s*["\'])([A-Za-z0-9-]{15,})(["\'])',
            lambda m: m.group(1) + self.obfuscate(m.group(2)) + m.group(3),
            text,
            flags=re.IGNORECASE
        )

        # Handle DIN numbers plain format
        text = re.sub(
            r'(\bdin[\s:=]+["\']?)([A-Za-z0-9-]{15,})',
            lambda m: m.group(1) + self.obfuscate(m.group(2)),
            text,
            flags=re.IGNORECASE
        )

        # Handle serial numbers in JSON format
        text = re.sub(
            r'(["\']serial_number["\']:\s*["\'])([A-Za-z0-9-]{8,})(["\'])',
            lambda m: m.group(1) + self.obfuscate(m.group(2)) + m.group(3),
            text,
            flags=re.IGNORECASE
        )

        # Handle serial numbers plain format
        text = re.sub(
            r'(serial[\s_]?(?:number)?[\s:=]+["\']?)([A-Za-z0-9-]{8,})',
            lambda m: m.group(1) + self.obfuscate(m.group(2)),
            text,
            flags=re.IGNORECASE
        )

        # Handle gateway IDs in JSON format
        text = re.sub(
            r'(["\']gateway_id["\']:\s*["\'])([A-Za-z0-9-]{15,})(["\'])',
            lambda m: m.group(1) + self.obfuscate(m.group(2)) + m.group(3),
            text,
            flags=re.IGNORECASE
        )

        # Handle gateway IDs plain format
        text = re.sub(
            r'(gateway[\s_]?(?:id)?[\s:=]+["\']?)([A-Za-z0-9-]{15,})',
            lambda m: m.group(1) + self.obfuscate(m.group(2)),
            text,
            flags=re.IGNORECASE
        )

        # Handle warp site numbers in JSON format
        text = re.sub(
            r'(["\']warp_site_number["\']:\s*["\'])([A-Za-z0-9-]{8,})(["\'])',
            lambda m: m.group(1) + self.obfuscate(m.group(2)) + m.group(3),
            text,
            flags=re.IGNORECASE
        )

        # Handle warp site numbers plain format
        text = re.sub(
            r'(warp[\s_]?(?:site)?(?:[\s_]?number)?[\s:=]+["\']?)([A-Za-z0-9-]{8,})',
            lambda m: m.group(1) + self.obfuscate(m.group(2)),
            text,
            flags=re.IGNORECASE
        )

        # Handle asset_site_id (UUIDs)
        text = re.sub(
            r'(["\']asset_site_id["\']:\s*["\'])([a-f0-9-]{36})(["\'])',
            lambda m: m.group(1) + self.obfuscate(m.group(2)) + m.group(3),
            text,
            flags=re.IGNORECASE
        )

        # Handle device_id (UUIDs)
        text = re.sub(
            r'(["\']device_id["\']:\s*["\'])([a-f0-9-]{36})(["\'])',
            lambda m: m.group(1) + self.obfuscate(m.group(2)) + m.group(3),
            text,
            flags=re.IGNORECASE
        )

        return text

    def _obfuscate_arg(self, arg: Any) -> Any:
        """Obfuscate an argument only if it contains sensitive data, preserving type otherwise."""
        return obfuscate_log_arg(arg, self._obfuscate_string)

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter log record to obfuscate sensitive data."""
        # Handle the message
        if record.msg:
            record.msg = self._obfuscate_string(str(record.msg))

        # Handle args if present (for %-style formatting)
        # Only convert args to strings if obfuscation patterns match
        # This preserves numeric types for format specifiers like %d and %.3f
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._obfuscate_arg(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._obfuscate_arg(a) for a in record.args)

        return True



_LOGGER = logging.getLogger(__name__)
_LOGGER.addFilter(SensitiveDataFilter())

def _flow_power_export_rate_dollars(config_entry: Any, state: str) -> float:
    """Return configured Flow Power Happy Hour export rate in $/kWh."""
    from ..const import CONF_FLOW_POWER_EXPORT_RATE, FLOW_POWER_EXPORT_RATES

    configured_rate = config_entry.options.get(
        CONF_FLOW_POWER_EXPORT_RATE,
        config_entry.data.get(CONF_FLOW_POWER_EXPORT_RATE),
    )
    if configured_rate not in (None, ""):
        try:
            return max(0.0, float(configured_rate) / 100)
        except (ValueError, TypeError):
            pass

    return FLOW_POWER_EXPORT_RATES.get(state, 0.0)


def _get_current_prices(hass: HomeAssistant, entry_id: str) -> tuple[float | None, float | None]:
    """Get current buy/sell prices in $/kWh for cost tracking.

    Priority: Amber coordinator → AEMO/Flow Power KWatch coordinator → tariff schedule.
    Returns (buy_price_per_kwh, sell_price_per_kwh) or (None, None) on failure.
    """
    try:
        entry_data = hass.data.get(DOMAIN, {}).get(entry_id, {})

        # Try Amber coordinator first (real-time market prices)
        amber_coordinator = entry_data.get("amber_coordinator")
        if amber_coordinator and amber_coordinator.data:
            current_prices = amber_coordinator.data.get("current", [])
            buy_cents = None
            sell_cents = None
            for price in current_prices:
                channel = price.get("channelType", "")
                if channel == "general":
                    buy_cents = price.get("perKwh")
                elif channel == "feedIn":
                    sell_cents = price.get("perKwh")
            if buy_cents is not None:
                # Amber perKwh is in cents → convert to $/kWh
                buy_dollar = buy_cents / 100.0
                sell_dollar = (sell_cents / 100.0) if sell_cents is not None else 0.0
                # Amber feedIn: negative = you earn, positive = you pay to export
                # Negate so sell_price is positive when earning, negative when paying
                return (buy_dollar, -sell_dollar)

        # Both Flow Power market-price sources publish the same Amber-compatible
        # current-price shape. KWatch-only installs do not create the AEMO
        # coordinator, so cost tracking must use their KWatch coordinator.
        aemo_coordinator = (
            entry_data.get("aemo_sensor_coordinator")
            or entry_data.get("flow_power_kwatch_coordinator")
        )
        if aemo_coordinator and aemo_coordinator.data:
            current_prices = aemo_coordinator.data.get("current", [])
            wholesale_cents = None
            sell_cents_raw = None
            for price in current_prices:
                channel = price.get("channelType", "")
                if channel == "general":
                    wholesale_cents = price.get("perKwh")
                elif channel == "feedIn":
                    sell_cents_raw = price.get("perKwh")
            if wholesale_cents is not None:
                config_entry = hass.config_entries.async_get_entry(entry_id)
                if config_entry:
                    from ..const import (
                        CONF_ELECTRICITY_PROVIDER,
                        CONF_PEA_ENABLED,
                        CONF_FLOW_POWER_BASE_RATE,
                        CONF_PEA_CUSTOM_VALUE,
                        CONF_FLOW_POWER_STATE,
                        FLOW_POWER_DEFAULT_BASE_RATE,
                        FLOW_POWER_HAPPY_HOUR_PERIODS,
                    )
                    from ..flow_power_pricing import (
                        calculate_flow_power_pea,
                        resolve_flow_power_pricing_context,
                    )
                    provider = config_entry.options.get(
                        CONF_ELECTRICITY_PROVIDER,
                        config_entry.data.get(CONF_ELECTRICITY_PROVIDER, ""),
                    )
                    if provider == "flow_power":
                        pea_enabled = config_entry.options.get(CONF_PEA_ENABLED, True)
                        fp_base_rate = config_entry.options.get(
                            CONF_FLOW_POWER_BASE_RATE, FLOW_POWER_DEFAULT_BASE_RATE
                        )
                        fp_custom_pea = config_entry.options.get(CONF_PEA_CUSTOM_VALUE)
                        try:
                            fp_custom_pea_value = (
                                float(fp_custom_pea)
                                if fp_custom_pea not in (None, "")
                                else None
                            )
                        except (TypeError, ValueError):
                            fp_custom_pea_value = None
                        if fp_custom_pea_value is not None:
                            pea = fp_custom_pea_value
                        elif pea_enabled:
                            pricing = resolve_flow_power_pricing_context(
                                config_entry.options,
                                config_entry.data,
                                entry_data,
                            )
                            pea = calculate_flow_power_pea(
                                wholesale_cents,
                                pricing,
                                tariff_rate=entry_data.get("fp_tariff_rate"),
                                avg_daily_tariff=entry_data.get("fp_avg_daily_tariff"),
                            )
                        else:
                            pea = 0.0
                        buy_cents_fp = max(0.0, fp_base_rate + pea)
                        # Export: Flow Power pays a flat happy hour rate, not the AEMO spot price.
                        # The AEMO feedIn channel reflects the wholesale price, which is unrelated
                        # to the fixed 45c/kWh happy hour credit Flow Power actually pays.
                        fp_state = config_entry.options.get(
                            CONF_FLOW_POWER_STATE,
                            config_entry.data.get(CONF_FLOW_POWER_STATE, "QLD1"),
                        )
                        now_local = dt_util.now()
                        period_key = f"PERIOD_{now_local.hour:02d}_{(now_local.minute // 30) * 30:02d}"
                        sell_dollar_fp = (
                            _flow_power_export_rate_dollars(config_entry, fp_state)
                            if period_key in FLOW_POWER_HAPPY_HOUR_PERIODS
                            else 0.0
                        )
                        return (buy_cents_fp / 100.0, sell_dollar_fp)
                    else:
                        # Generic AEMO (non-Flow-Power): wholesale price is the retail price
                        buy_dollar = wholesale_cents / 100.0
                        sell_dollar = max(0.0, -(sell_cents_raw or 0)) / 100.0
                        return (buy_dollar, sell_dollar)

        # Fall back to tariff schedule (TOU rates).
        # Note: buy_prices/sell_prices in tariff_schedule are stored in $/kWh (Tesla
        # tariff format). get_current_price_from_tariff_schedule() multiplies by 100
        # internally for the PERIOD_HH_MM branch, so the return value is always c/kWh.
        tariff_schedule = entry_data.get("tariff_schedule")
        if tariff_schedule:
            from .. import get_current_price_from_tariff_schedule
            buy_cents, sell_cents, _ = get_current_price_from_tariff_schedule(tariff_schedule)
            return (buy_cents / 100.0, sell_cents / 100.0)

    except Exception as exc:
        _LOGGER.debug("Failed to get current prices for cost tracking: %s", exc)

    return (None, None)


def _parse_retry_after(response: aiohttp.ClientResponse) -> float | None:
    """Parse Retry-After header from an HTTP response.

    Returns delay in seconds, or None if header is missing/invalid.
    Supports both delta-seconds and HTTP-date formats.
    """
    retry_after = response.headers.get("Retry-After")
    if not retry_after:
        return None
    try:
        # Try delta-seconds first (e.g. "30")
        return max(1.0, min(float(retry_after), 300.0))  # Clamp 1-300s
    except (ValueError, TypeError):
        pass
    try:
        # Try HTTP-date format (e.g. "Tue, 11 Feb 2026 03:00:00 GMT")
        from email.utils import parsedate_to_datetime
        retry_date = parsedate_to_datetime(retry_after)
        from homeassistant.util import dt as dt_util
        delay = (retry_date - dt_util.utcnow()).total_seconds()
        return max(1.0, min(delay, 300.0))  # Clamp 1-300s
    except (ValueError, TypeError):
        return None


async def _fetch_with_retry(
    session: aiohttp.ClientSession,
    url: str,
    headers: dict,
    max_retries: int = 3,
    timeout_seconds: int = 60,
    raise_auth_failed: bool = True,
    **kwargs
) -> dict[str, Any]:
    """Fetch data with exponential backoff retry logic.

    Respects Retry-After headers from 429/503 responses. Retries on
    5xx server errors and 429 rate limits; fails immediately on other 4xx.

    Args:
        session: aiohttp client session
        url: URL to fetch
        headers: Request headers
        max_retries: Maximum number of retry attempts (default: 3)
        timeout_seconds: Request timeout in seconds (default: 60)
        raise_auth_failed: Whether 401 responses should raise
            ConfigEntryAuthFailed instead of UpdateFailed
        **kwargs: Additional arguments to pass to session.get()

    Returns:
        JSON response data

    Raises:
        UpdateFailed: If all retries fail
    """
    last_error = None
    retry_after_delay = None  # Set by Retry-After header

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                # Use Retry-After delay if available, otherwise exponential backoff
                wait_time = retry_after_delay or (2 ** attempt)
                retry_after_delay = None  # Reset for next attempt
                _LOGGER.info(
                    "Retry attempt %d/%d after %.0fs delay",
                    attempt + 1, max_retries, wait_time,
                )
                await asyncio.sleep(wait_time)

            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                **kwargs
            ) as response:
                if response.status == 200:
                    return await response.json()

                error_text = await response.text()

                if response.status == 429:
                    # Rate limited — retry with Retry-After if provided
                    retry_after_delay = _parse_retry_after(response)
                    _LOGGER.warning(
                        "Rate limited 429 (attempt %d/%d): %s (retry-after: %s)",
                        attempt + 1, max_retries, error_text[:200],
                        f"{retry_after_delay:.0f}s" if retry_after_delay else "not set",
                    )
                    last_error = UpdateFailed(f"Rate limited: 429")
                    continue

                if response.status >= 500:
                    # Server error — retry, respect Retry-After if present
                    retry_after_delay = _parse_retry_after(response)
                    _LOGGER.warning(
                        "Server error (attempt %d/%d): %s - %s",
                        attempt + 1, max_retries, response.status, error_text[:200],
                    )
                    last_error = UpdateFailed(f"Server error: {response.status}")
                    continue

                # 401 → token expired/revoked. Direct token providers should
                # trigger HA reauth. Fleet API tokens are owned/refreshed by
                # the separate tesla_fleet integration, so callers can treat
                # them as transient stale-token failures instead.
                if response.status == 401:
                    if raise_auth_failed:
                        _LOGGER.warning(
                            "Authentication failed (401) — triggering reauth: %s",
                            error_text[:200],
                        )
                        raise ConfigEntryAuthFailed(f"Token rejected by upstream: {error_text[:200]}")
                    _LOGGER.warning(
                        "Authentication failed (401) — token may be refreshing upstream: %s",
                        error_text[:200],
                    )
                    raise UpdateFailed(f"Authentication failed: 401 - {error_text[:200]}")

                # Other 4xx client errors — don't retry
                raise UpdateFailed(f"Client error {response.status}: {error_text}")

        except aiohttp.ClientError as err:
            _LOGGER.warning(
                "Network error (attempt %d/%d): %s",
                attempt + 1, max_retries, err,
            )
            last_error = UpdateFailed(f"Network error: {err}")
            continue

        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Timeout error (attempt %d/%d): Request exceeded %ds",
                attempt + 1, max_retries, timeout_seconds,
            )
            last_error = UpdateFailed(f"Timeout after {timeout_seconds}s")
            continue

    # All retries failed
    raise last_error or UpdateFailed("All retry attempts failed")


