"""Top-level façade the coordinator talks to for local control.

The coordinator does not need to know about mDNS, TLS, handshakes or the LEEL
JSON protocol; it only cares about:

- "is the LAN channel ready?"
- "send this control command and tell me if it worked"
- "let me know when device state pushes arrive"

:class:`LeelenLanManager` orchestrates discovery, the underlying
:class:`LeelenLanClient`, and the routing of push messages back into the
coordinator.  Manual gateway configuration (entered through the options flow
when mDNS discovery fails) overrides mDNS discovery entirely.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .client import (
    DEFAULT_PORT,
    STATE_READY,
    GatewayInfo,
    LeelenLanClient,
)
from .discovery import async_discover_gateways

_LOGGER = logging.getLogger(__name__)

DISCOVERY_INTERVAL_SECONDS = 600.0
FIRST_DISCOVERY_TIMEOUT_SECONDS = 8.0


class LeelenLanManager:
    """Own the Leelen LAN client lifecycle and expose control/push callbacks."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        group_id: str,
        on_push: Any,
        on_state: Any,
    ) -> None:
        self._hass = hass
        # HA's config_entries storage may persist ``groupId`` as an int (the
        # cloud login response returns it as a number); the on-wire JSON,
        # however, must carry a string.  Coerce at the boundary so the
        # handshake's ``src``/``groupId``/``dst`` never lose their quotes.
        self._group_id = str(group_id) if group_id is not None else None
        self._on_push = on_push
        self._on_state = on_state
        self._client: LeelenLanClient | None = None
        self._client_task: asyncio.Task | None = None
        self._discovery_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._manual_gateway: GatewayInfo | None = None
        self._known_did: str | None = None

    @property
    def is_ready(self) -> bool:
        return self._client is not None and self._client.is_ready

    @property
    def gateway(self) -> GatewayInfo | None:
        return self._client.gateway if self._client else None

    def set_manual_gateway(self, host: str, port: int = DEFAULT_PORT, did: str | None = None) -> None:
        """Pin a specific gateway; disables mDNS discovery."""
        self._manual_gateway = GatewayInfo(
            host=host,
            port=port,
            did=str(did) if did is not None else None,
            group_id=self._group_id,
        )

    def set_known_gateway_did(self, did: str) -> None:
        """Pre-seed the gateway did for the mDNS discovery path.

        mDNS TXT records on the Leelen gateway do not always carry the did, so
        the caller (which already knows the did from the cloud device list)
        can hand it to us here.  When discovery reports a gateway without a
        did, we fall back to this value.
        """
        self._known_did = str(did) if did is not None else None

    async def start(self) -> None:
        self._stop_event.clear()
        if self._manual_gateway is not None:
            await self._swap_client(self._manual_gateway)
        else:
            self._discovery_task = asyncio.create_task(self._discovery_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        for task in (self._discovery_task, self._client_task):
            if task is not None and not task.done():
                task.cancel()
        if self._client is not None:
            await self._client.stop()
            self._client = None

    async def ctrl_fiids(
        self,
        *,
        did: str,
        direct_did: str,
        siid: int,
        fiid: int,
        value: dict[str, Any],
    ) -> bool:
        """Send a ``dmgr.ctrlFIIDS`` command; return ``True`` on ack.

        The transparent response envelope is::

            {"ack": 1, "seq": N, "params": {"topic": "...",
                "payload": {"method": "dmgr.ctrlFIIDS", "result": 1, ...}}}

        Note the response has **no** top-level ``method`` field (unlike the
        request, which is ``"transparent"``); the inner ``params.payload``
        carries the echoed ``dmgr.ctrlFIIDS`` method and the device's
        ``result`` flag.  ``result=1`` means the device executed the command.
        """
        client = self._client
        if client is None or not client.is_ready:
            return False
        try:
            response = await client.ctrl_fiids(
                did=str(did),
                direct_did=str(direct_did),
                siid=siid,
                fiid=fiid,
                value=value,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Leelen LAN 控制失败 (%s): %s", did, exc)
            return False
        if response is None:
            return False
        payload = response.get("payload_json") or {}
        if payload.get("ack") != 1:
            _LOGGER.info(
                "[lan-ctrl] 网关拒绝控制 ack=%s did=%s fiid=%s",
                payload.get("ack"), did, fiid,
            )
            return False
        inner = payload.get("params", {}).get("payload") or {}
        if inner.get("method") != "dmgr.ctrlFIIDS":
            _LOGGER.info(
                "[lan-ctrl] 响应 method 不匹配 got=%s did=%s",
                inner.get("method"), did,
            )
            return False
        result = inner.get("result", 1)
        if result != 1:
            _LOGGER.info(
                "[lan-ctrl] 设备执行失败 result=%s did=%s fiid=%s",
                result, did, fiid,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Internal: discovery + client lifecycle
    # ------------------------------------------------------------------

    async def _discovery_loop(self) -> None:
        first_pass = True
        while not self._stop_event.is_set():
            timeout = (
                FIRST_DISCOVERY_TIMEOUT_SECONDS
                if first_pass
                else DISCOVERY_INTERVAL_SECONDS
            )
            first_pass = False
            _LOGGER.debug(
                "[lan-disco] 启动 mDNS 扫描 timeout=%ss", timeout
            )
            try:
                gateways = await async_discover_gateways(
                    self._hass, group_id=self._group_id, timeout=timeout
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Leelen LAN 发现异常: %s", exc)
                gateways = []
            _LOGGER.debug(
                "[lan-disco] 发现 %s 个网关: %s",
                len(gateways),
                [str(g) for g in gateways],
            )
            if self._stop_event.is_set():
                break
            if gateways:
                gateway = gateways[0]
                if gateway.did is None and self._known_did:
                    gateway.did = self._known_did
                    _LOGGER.debug(
                        "[lan-disco] 用 cloud did 兜底: %s", gateway.did
                    )
                await self._swap_client(gateway)
            # Wait for the next discovery round; exit early on stop.
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=DISCOVERY_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                continue
            break

    async def _swap_client(self, gateway: GatewayInfo) -> None:
        if self._client is not None and self._client.gateway == gateway:
            _LOGGER.debug("[lan-swap] 网关未变 %s", gateway)
            return
        if self._client is not None:
            _LOGGER.debug("[lan-swap] 关闭旧 client %s", self._client.gateway)
            await self._client.stop()
            self._client = None
        _LOGGER.info(
            "[lan-swap] 启动新 client %s (did=%s)", gateway, gateway.did
        )
        self._client = LeelenLanClient(
            gateway,
            on_push=self._handle_push,
            on_state=self._handle_state,
        )
        await self._client.start()

    def _handle_push(self, payload: dict[str, Any]) -> None:
        try:
            self._on_push(payload)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Leelen LAN push 回调异常")

    def _handle_state(self, state: str) -> None:
        try:
            self._on_state(state)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Leelen LAN state 回调异常")
