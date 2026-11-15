"""Async TLS client for the Leelen iotlan LEEL JSON protocol.

Mirrors the official app's `com.leelen.smarthouse.iotlan.ctrl.IotLanRTUtil`
connection state machine:

1. ``bind``      (enc=0) — params={src, dst, groupId}                    → ack:1
2. ``random``    (enc=0) — params={src, dst}                             → params.random
3. derive ``password = HMAC-MD5(key=prefix+random, msg=groupId)`` (32 uppercase hex)
4. derive ``aes_key = hex_to_bytes(password)``                            (16 bytes)
5. ``login``     (enc=1) — params={src, dst, username=groupId, password}
6. ``heartbeat`` (enc=1) — params={interval:7}                           (recurring)
7. ``transparent`` (enc=1) — DevRequest(method="transparent",
                                        params=DevPayload(topic, Payload(seq,
                                        "dmgr.ctrlFIIDS", {did, directDid,
                                        siid, fiids:[{fiid, value:{...}}]},
                                        dst=directDid, src=groupId)), seq=N)

The official app's transient-field quirk (DevRequest.dst/encryptionType/
mainGateway are never serialised) is handled in ``protocol.py``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
from dataclasses import dataclass, field
from typing import Any

from . import crypto, protocol

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 49853
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_REQUEST_TIMEOUT = 8.0
HEARTBEAT_INTERVAL_SECONDS = 7
RECONNECT_MIN_DELAY = 5.0
RECONNECT_MAX_DELAY = 60.0

# Methods that the gateway pushes asynchronously (server-initiated).  The
# gateway always wraps these inside a `transparent` envelope
# (DevRequest → DevPayload → Payload), so the dispatcher walks the inner
# Payload to find them.
PUSH_METHODS = {"dmgr.notifyFIIDS", "dmgr.deviceOnline", "dmgr.deviceOffline"}

# Login is the only state we need to track: callers want to know whether the
# connection is ready to issue control commands.
STATE_DISCONNECTED = "disconnected"
STATE_CONNECTING = "connecting"
STATE_LOGIN = "login"
STATE_READY = "ready"


@dataclass
class GatewayInfo:
    """Identity of one discovered Leelen gateway."""

    host: str
    port: int = DEFAULT_PORT
    did: str | None = None
    group_id: str | None = None

    def __str__(self) -> str:
        return f"{self.host}:{self.port}"


class LeelenLanClient:
    """Manage one TLS connection to a Leelen gateway and serialise requests.

    The client is single-connection: only one in-flight request at a time,
    because the protocol's framing is request/response keyed only by ``seq``.
    Long-lived pushes (``dmgr.notifyFIIDS``) are dispatched to a separate
    callback so they do not collide with pending control requests.
    """

    def __init__(
        self,
        gateway: GatewayInfo,
        *,
        on_push: Any | None = None,
        on_state: Any | None = None,
    ) -> None:
        self._gateway = gateway
        self._on_push = on_push
        self._on_state = on_state

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._seq = -1
        self._aes_key: bytes | None = None
        self._password: str | None = None
        self._state = STATE_DISCONNECTED
        self._stop_event = asyncio.Event()
        self._reader_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_request_id = 0
        self._request_lock = asyncio.Lock()
        # Coerce to str: HA storage may hand us an int ``groupId`` and the
        # on-wire JSON requires strings (a bare int drops the quotes and the
        # gateway rejects the bind with ``ack=0``).
        self._group_id = (
            str(gateway.group_id) if gateway.group_id is not None else None
        )
        self._did = str(gateway.did) if gateway.did is not None else None

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_ready(self) -> bool:
        return self._state == STATE_READY

    @property
    def gateway(self) -> GatewayInfo:
        return self._gateway

    async def start(self) -> None:
        """Start the reconnect loop in the background."""
        if self._reconnect_task is not None:
            return
        self._stop_event.clear()
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def stop(self) -> None:
        """Cancel the reconnect loop and tear down the active connection."""
        self._stop_event.set()
        for task in (self._reconnect_task, self._reader_task, self._heartbeat_task):
            if task is not None and not task.done():
                task.cancel()
        await self._close_writer()

    async def ensure_login(self) -> bool:
        """Best-effort: wait until the connection is READY or give up."""
        for _ in range(20):
            if self._state == STATE_READY:
                return True
            if self._stop_event.is_set():
                return False
            await asyncio.sleep(0.25)
        return False

    async def ctrl_fiids(
        self,
        *,
        did: str,
        direct_did: str,
        siid: int,
        fiid: int,
        value: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Send one ``dmgr.ctrlFIIDS`` transparent request and await the ack."""
        if not self.is_ready or self._group_id is None:
            _LOGGER.debug(
                "[lan-ctrl] 跳过: ready=%s group_id=%s",
                self.is_ready, bool(self._group_id),
            )
            return None
        request = protocol.build_ctrl_dev_request(
            seq=self._next_seq(),
            did=did,
            direct_did=direct_did,
            siid=siid,
            fiid=fiid,
            value=value,
            group_id=self._group_id,
        )
        payload = protocol.serialise_dev_request(request)
        _LOGGER.debug(
            "[lan-ctrl] 发送 seq=%s did=%s siid=%s fiid=%s value=%s",
            request.seq, did, siid, fiid, value,
        )
        try:
            return await self._send_encrypted(request.seq, payload)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("[lan-ctrl] 发送异常 seq=%s: %s", request.seq, exc)
            raise

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def _reconnect_loop(self) -> None:
        delay = RECONNECT_MIN_DELAY
        while not self._stop_event.is_set():
            try:
                await self._connect_and_login()
                delay = RECONNECT_MIN_DELAY
                # Wait until something breaks the connection.
                await self._wait_for_disconnect()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "Leelen LAN 网关 %s 连接失败: %s", self._gateway, exc
                )
            finally:
                await self._close_writer()

            if self._stop_event.is_set():
                break
            _LOGGER.info(
                "Leelen LAN %s 秒后重连 %s", int(delay), self._gateway
            )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _connect_and_login(self) -> None:
        self._set_state(STATE_CONNECTING)
        await self._open_socket()
        # Reset the per-connection seq counter: a fresh TLS session starts a
        # fresh handshake, and reusing the old seq would orphan any in-flight
        # futures from the previous attempt.
        self._seq = -1
        # Reader loop MUST start before the handshake: bind/random/login each
        # await a future that is resolved by the reader dispatching the
        # matching response frame.
        self._reader_task = asyncio.create_task(self._reader_loop())
        try:
            await self._do_bind()
            random_str = await self._do_random()
            self._password = crypto.compute_password(random_str, self._group_id)
            self._aes_key = crypto.hex_to_bytes(self._password)
            await self._do_login()
        except Exception:
            if self._reader_task is not None and not self._reader_task.done():
                self._reader_task.cancel()
            self._reader_task = None
            raise
        self._set_state(STATE_READY)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        _LOGGER.info(
            "Leelen LAN 已登录 %s (did=%s, group=%s)",
            self._gateway,
            self._did,
            self._group_id,
        )

    async def _wait_for_disconnect(self) -> None:
        if self._reader_task is None:
            return
        try:
            await self._reader_task
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass

    async def _open_socket(self) -> None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host=self._gateway.host,
                    port=self._gateway.port,
                    ssl=ctx,
                    server_hostname=self._gateway.host,
                ),
                timeout=DEFAULT_CONNECT_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise ConnectionError(
                f"无法连接到 Leelen 网关 {self._gateway}: {exc}"
            ) from exc

    async def _close_writer(self) -> None:
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        # reader_task is cancelled by stop(); during reconnect it exits on its
        # own when the socket closes.
        writer, self._writer = self._writer, None
        self._reader = None
        self._aes_key = None
        self._password = None
        self._fail_pending(ConnectionError("连接已断开"))
        self._set_state(STATE_DISCONNECTED)
        if writer is None:
            return
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Handshake (bind / random / login)
    # ------------------------------------------------------------------

    async def _do_bind(self) -> None:
        seq = self._next_seq()
        params = {"src": self._group_id, "dst": self._did, "groupId": self._group_id}
        _LOGGER.info(
            "[lan-bind] seq=%s did=%s group=%s", seq, self._did, self._group_id
        )
        response = await self._send_plaintext(seq, "bind", params)
        payload = response.get("payload_json") or {}
        if payload.get("ack") != 1:
            raise ConnectionError(
                f"网关 bind 失败 ack={payload.get('ack')}: {response}"
            )

    async def _do_random(self) -> str:
        seq = self._next_seq()
        params = {"src": self._group_id, "dst": self._did}
        response = await self._send_plaintext(seq, "random", params)
        payload = response.get("payload_json") or {}
        if payload.get("ack") != 1:
            raise ConnectionError(
                f"网关 random 失败 ack={payload.get('ack')}: {response}"
            )
        params_resp = payload.get("params", {})
        random_str = params_resp.get("random")
        if not random_str:
            raise ConnectionError(f"网关 random 响应缺少 random 字段: {response}")
        _LOGGER.info("[lan-random] seq=%s random=%s", seq, random_str)
        return random_str

    async def _do_login(self) -> None:
        seq = self._next_seq()
        response = await self._send_encrypted(
            seq,
            json.dumps(
                {
                    "method": "login",
                    "params": {
                        "src": self._group_id,
                        "dst": self._did,
                        "username": self._group_id,
                        "password": self._password,
                    },
                    "seq": seq,
                },
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8"),
        )
        payload = response.get("payload_json") or {}
        if payload.get("ack") != 1:
            raise ConnectionError(
                f"网关 login 失败 ack={payload.get('ack')}: {response}"
            )
        _LOGGER.info("[lan-login] seq=%s ok", seq)

    # ------------------------------------------------------------------
    # Reader + heartbeat loops
    # ------------------------------------------------------------------

    async def _reader_loop(self) -> None:
        reader = self._reader
        if reader is None:
            return
        buffer = bytearray()
        while not self._stop_event.is_set():
            try:
                chunk = await reader.read(8192)
            except (ConnectionError, asyncio.TimeoutError, OSError):
                break
            except asyncio.CancelledError:
                raise
            if not chunk:
                _LOGGER.debug("[lan-reader] socket 关闭 %s", self._gateway)
                break
            _LOGGER.debug(
                "[lan-reader] 收到 %s 字节 (累计 buffer=%s)",
                len(chunk), len(buffer) + len(chunk),
            )
            buffer.extend(chunk)
            buffer = self._drain_frames(bytes(buffer))
        _LOGGER.debug("Leelen LAN reader 退出 %s", self._gateway)

    def _drain_frames(self, buffer: bytes) -> bytearray:
        """Slice ``buffer`` into complete LEEL frames; return leftover bytes."""
        leftover = bytearray(buffer)
        while True:
            frame, remaining = self._take_one_frame(leftover)
            if frame is None:
                return leftover
            leftover = bytearray(remaining)
            _LOGGER.debug(
                "[lan-frame] 取出帧 len=%s crc_byte=0x%02x",
                len(frame), frame[-1] if frame else 0,
            )
            try:
                parsed = protocol.decode_iotlan(frame, self._aes_key)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Leelen LAN 帧解析失败: %s", exc)
                continue
            self._dispatch_parsed(parsed)

    @staticmethod
    def _take_one_frame(buffer: bytes) -> tuple[bytes | None, bytes]:
        if len(buffer) < 9 or buffer[:4] != protocol.SYNC_HEADER:
            return None, buffer
        length = int.from_bytes(buffer[4:8], "little")
        end = 8 + length
        if len(buffer) < end:
            return None, buffer
        return buffer[:end], buffer[end:]

    def _dispatch_parsed(self, parsed: dict[str, Any]) -> None:
        payload = parsed.get("payload_json")
        if not isinstance(payload, dict):
            _LOGGER.debug("[lan-dispatch] 非 JSON payload: %s", parsed)
            return
        seq = payload.get("seq")
        method = payload.get("method")
        ack = payload.get("ack")
        _LOGGER.debug(
            "[lan-dispatch] seq=%s method=%s ack=%s enc=%s",
            seq, method, ack, parsed.get("encryptionType"),
        )
        # Gateway pushes arrive as transparent envelopes wrapping DevPayload
        # → Payload(method ∈ PUSH_METHODS).  Unwrap them here so the
        # coordinator only has to deal with the inner Payload.
        if method == "transparent":
            inner = self._unwrap_transparent(payload)
            if inner is not None:
                inner_method = inner.get("method")
                _LOGGER.debug(
                    "[lan-dispatch] transparent inner method=%s seq=%s",
                    inner_method, inner.get("seq"),
                )
                if inner_method in PUSH_METHODS:
                    self._handle_push(inner)
                    return
        # Acks for our own transparent control requests share the same
        # transparent envelope, but the inner method is dmgr.ctrlFIIDS —
        # route them to the waiting future keyed by outer seq.
        if isinstance(seq, int):
            future = self._pending.pop(seq, None)
            if future is not None and not future.done():
                _LOGGER.debug(
                    "[lan-dispatch] 路由响应到 future seq=%s", seq
                )
                future.set_result(parsed)
            else:
                _LOGGER.debug(
                    "[lan-dispatch] 无 pending future seq=%s (可能已超时或为推送)",
                    seq,
                )

    @staticmethod
    def _unwrap_transparent(payload: dict[str, Any]) -> dict[str, Any] | None:
        params = payload.get("params")
        if not isinstance(params, dict):
            return None
        inner = params.get("payload")
        if isinstance(inner, dict):
            return inner
        return None

    def _handle_push(self, payload: dict[str, Any]) -> None:
        if self._on_push is None:
            return
        try:
            self._on_push(payload)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Leelen LAN push 回调异常")

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                if not self.is_ready:
                    continue
                seq = self._next_seq()
                payload = json.dumps(
                    {
                        "method": "heartbeat",
                        "params": {"interval": HEARTBEAT_INTERVAL_SECONDS},
                        "seq": seq,
                    },
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
                ciphertext = crypto.aes_encrypt(payload, self._aes_key)
                frame = protocol.encode_iotlan(
                    ciphertext, encryption_type=1
                )
                writer = self._writer
                if writer is None:
                    continue
                try:
                    writer.write(frame)
                    await asyncio.wait_for(
                        writer.drain(), timeout=DEFAULT_REQUEST_TIMEOUT
                    )
                except (asyncio.TimeoutError, ConnectionError, OSError):
                    # The reconnect loop will pick up the dropped connection.
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Leelen LAN heartbeat 退出: %s", exc)

    # ------------------------------------------------------------------
    # Low-level send helpers
    # ------------------------------------------------------------------

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def _send_plaintext(
        self,
        seq: int,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        payload = json.dumps(
            {"method": method, "params": params, "seq": seq},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        _LOGGER.debug(
            "[lan-plain] seq=%s method=%s payload=%s",
            seq, method, payload.decode("utf-8"),
        )
        return await self._send(seq, payload, encryption_type=0)

    async def _send_encrypted(self, seq: int, payload: bytes) -> dict[str, Any]:
        if self._aes_key is None:
            raise RuntimeError("AES key not derived yet; login incomplete")
        ciphertext = crypto.aes_encrypt(payload, self._aes_key)
        return await self._send(seq, ciphertext, encryption_type=1)

    async def _send(
        self,
        seq: int,
        payload: bytes,
        *,
        encryption_type: int,
    ) -> dict[str, Any]:
        async with self._request_lock:
            writer = self._writer
            if writer is None:
                raise ConnectionError("Leelen LAN 连接已断开")
            frame = protocol.encode_iotlan(
                payload, encryption_type=encryption_type
            )
            _LOGGER.debug(
                "[lan-send] seq=%s enc=%s frame_len=%s payload_len=%s",
                seq, encryption_type, len(frame), len(payload),
            )
            loop = asyncio.get_running_loop()
            future: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._pending[seq] = future
            writer.write(frame)
            try:
                await asyncio.wait_for(writer.drain(), timeout=DEFAULT_REQUEST_TIMEOUT)
            except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
                self._pending.pop(seq, None)
                raise ConnectionError(f"Leelen LAN 发送失败: {exc}") from exc
            try:
                response = await asyncio.wait_for(future, timeout=DEFAULT_REQUEST_TIMEOUT)
                _LOGGER.debug(
                    "[lan-send] seq=%s 收到响应 ack=%s",
                    seq, (response.get("payload_json") or {}).get("ack"),
                )
                return response
            except asyncio.TimeoutError:
                self._pending.pop(seq, None)
                raise ConnectionError(f"Leelen LAN 等待响应超时 (seq={seq})")
            finally:
                self._pending.pop(seq, None)

    def _fail_pending(self, exc: BaseException) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        if self._on_state is not None:
            try:
                self._on_state(state)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Leelen LAN state 回调异常")
