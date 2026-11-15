"""mDNS-based discovery for Leelen gateways.

The official Leelen app advertises its gateway via the ``_iot-gateway._tcp.local.``
mDNS service.  Home Assistant ships a shared ``Zeroconf`` instance reachable
through ``homeassistant.components.zeroconf.async_get_instance``; reusing it
keeps us off port 5353 ourselves and integrates with HA's existing browser
infrastructure.

The gateway's mDNS TXT record carries the device id we need to authenticate
the iotlan handshake (``did``).  We pair that with the ``groupId`` already
stored on the config entry (from the cloud login) and hand the result off to
:class:`LeelenLanClient`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant

from .client import DEFAULT_PORT, GatewayInfo

_LOGGER = logging.getLogger(__name__)

GATEWAY_SERVICE_TYPE = "_iot-gateway._tcp.local."
DISCOVERY_TIMEOUT_SECONDS = 10.0

# TXT-record keys observed on the Leelen gateway (lower-cased by python-zeroconf).
DID_TXT_KEYS = ("did", "devid", "deviceid", "sn")


class _ServiceCollector:
    """Collect every service that gets announced during the discovery window."""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.names: list[str] = []

    def add_service(self, zeroconf: Any, service_type: str, name: str) -> None:
        self.update_service(zeroconf, service_type, name)

    def update_service(self, zeroconf: Any, service_type: str, name: str) -> None:
        if name in self.seen:
            return
        self.seen.add(name)
        self.names.append(name)


async def async_discover_gateways(
    hass: HomeAssistant,
    *,
    group_id: str | None = None,
    timeout: float = DISCOVERY_TIMEOUT_SECONDS,
) -> list[GatewayInfo]:
    """Discover Leelen gateways on the local network.

    Returns one :class:`GatewayInfo` per unique mDNS service seen.  ``group_id``
    is attached to every result so the caller can hand the info straight to
    :class:`LeelenLanClient`.
    """
    try:
        zeroconf_instance = await zeroconf.async_get_instance(hass)
    except Exception as exc:  # noqa: BLE001 - zeroconf integration not loaded
        _LOGGER.warning("Leelen LAN 发现失败: zeroconf 不可用 (%s)", exc)
        return []

    collector = _ServiceCollector()
    # python-zeroconf's ServiceBrowser runs its own thread; we just need to
    # keep it alive for the discovery window.
    try:
        from zeroconf import ServiceBrowser  # type: ignore[import-not-found]
    except ImportError:
        _LOGGER.warning("Leelen LAN 发现失败: 缺少 zeroconf 库")
        return []

    browser = ServiceBrowser(zeroconf_instance, GATEWAY_SERVICE_TYPE, listener=collector)
    try:
        await asyncio.sleep(timeout)
    finally:
        try:
            browser.cancel()
        except Exception:  # noqa: BLE001
            pass

    return await hass.async_add_executor_job(
        _resolve_gateways, zeroconf_instance, collector.names, group_id
    )


def _resolve_gateways(
    zeroconf_instance: Any,
    service_names: list[str],
    group_id: str | None,
) -> list[GatewayInfo]:
    """Resolve service names to ``GatewayInfo`` records in a worker thread."""
    results: list[GatewayInfo] = []
    for name in service_names:
        info = zeroconf_instance.get_service_info(GATEWAY_SERVICE_TYPE, name, timeout=2000)
        if info is None:
            _LOGGER.debug("[lan-disco] %s → get_service_info 返回 None", name)
            continue
        host = _extract_host(info)
        port = info.port or DEFAULT_PORT
        properties = getattr(info, "properties", None) or {}
        _LOGGER.debug(
            "[lan-disco] %s host=%s port=%s properties=%s",
            name, host, port,
            {k: (v.decode("utf-8", "replace") if isinstance(v, bytes) else v)
             for k, v in properties.items()},
        )
        if not host:
            _LOGGER.debug("[lan-disco] %s 跳过: 无 host", name)
            continue
        did = _extract_did(info)
        results.append(GatewayInfo(host=host, port=port, did=did, group_id=group_id))
        _LOGGER.info(
            "Leelen LAN 发现网关 %s:%s (did=%s)", host, port, did or "?"
        )
    return results


def _extract_host(info: Any) -> str | None:
    # python-zeroconf ≥0.32 exposes parsed_addresses(); fall back to the
    # legacy addresses attribute for older installs.
    addresses = getattr(info, "parsed_addresses", None)
    if addresses:
        try:
            return addresses()[0] if callable(addresses) else addresses[0]
        except Exception:  # noqa: BLE001
            pass
    if getattr(info, "addresses", None):
        for raw in info.addresses:
            try:
                import socket

                return socket.inet_ntoa(raw)
            except (OSError, ValueError):
                continue
    return None


def _extract_did(info: Any) -> str | None:
    properties = getattr(info, "properties", None) or {}
    # python-zeroconf returns dict[str, bytes] in newer versions.
    decoded: dict[str, str] = {}
    for key, value in properties.items():
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                continue
        decoded[str(key).lower()] = str(value)
    for key in DID_TXT_KEYS:
        if decoded.get(key):
            return decoded[key]
    return None
