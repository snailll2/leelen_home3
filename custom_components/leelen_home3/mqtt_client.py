"""MQTT push transport for Leelen FIID state reports."""

from __future__ import annotations

import json
import logging
import ssl

from .coordinator import SERVICE_FIIDS

_LOGGER = logging.getLogger(__name__)

MQTT_HOST = "iot.leelen.com"
MQTT_PORT = 8883
MQTT_KEEPALIVE = 180
MQTT_RECONNECT_MIN_SECONDS = 15
MQTT_RECONNECT_MAX_SECONDS = 300

# 官方 App 的 MQTT 用户名前缀（反编译 TokenLoader：
# "a5e4x84a:" + accountId，密码为 accessToken，clientId 由
# refreshToken 接口下发）。
MQTT_USERNAME_PREFIX = "a5e4x84a:"


def build_mqtt_username(account_id):
    """Return the official app's MQTT username for one account."""
    return f"{MQTT_USERNAME_PREFIX}{account_id}"


class LeelenMqttClient:
    """Receive original-format Leelen MQTT state pushes."""

    def __init__(
        self,
        hass,
        coordinator,
        api,
        client_id,
        username,
        auto=False,
    ):
        import paho.mqtt.client as mqtt

        self._hass = hass
        self._coordinator = coordinator
        self._api = api
        self._client_id = client_id
        self._username = username
        # auto 模式下 clientId 来自 refreshToken 响应，可能在重连前已轮换
        self._auto = bool(auto)
        self._stopped = False

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        self._set_credentials()
        self._client.tls_set(cert_reqs=ssl.CERT_NONE)
        self._client.tls_insecure_set(True)
        self._client.reconnect_delay_set(
            min_delay=MQTT_RECONNECT_MIN_SECONDS,
            max_delay=MQTT_RECONNECT_MAX_SECONDS,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def start(self):
        """Start Paho's network loop without blocking Home Assistant."""
        self._stopped = False
        self._client.connect_async(
            MQTT_HOST,
            MQTT_PORT,
            keepalive=MQTT_KEEPALIVE,
        )
        self._client.loop_start()

    def stop(self):
        """Disconnect and stop Paho's network thread."""
        self._stopped = True
        self._client.disconnect()
        self._client.loop_stop()

    def _set_credentials(self):
        self._client.username_pw_set(
            self._username,
            self._api._access_token,
        )

    def _topics(self):
        topics = {f"lliot/receiver/{self._client_id}"}
        for device in self._coordinator.get_devices():
            did = device.get("dev_addr")
            if not did:
                continue
            for service in device.get("logic_srv") or []:
                if service.get("service_type") not in SERVICE_FIIDS:
                    continue
                siid = service.get("siid")
                if siid is not None:
                    topics.add(f"lliot/fiids_report/{did}/{siid}")
        return sorted(topics)

    def _on_connect(
        self,
        client,
        userdata,
        connect_flags,
        reason_code,
        properties,
    ):
        if reason_code != 0:
            _LOGGER.warning("Leelen MQTT 连接被拒绝: %s", reason_code)
            self._dispatch_connection(False)
            return

        topics = self._topics()
        if topics:
            client.subscribe([(topic, 0) for topic in topics])
        _LOGGER.info("Leelen MQTT 已订阅 %s 个状态主题", len(topics))
        self._dispatch_connection(True)

    def _on_disconnect(
        self,
        client,
        userdata,
        disconnect_flags,
        reason_code,
        properties,
    ):
        self._dispatch_connection(False)
        if not self._stopped:
            # The REST client may have refreshed the access token since the
            # previous connection. Paho will use this value on its next retry.
            self._set_credentials()
            if self._auto:
                latest_client_id = (
                    getattr(self._api, "_client_id", "") or self._client_id
                )
                if latest_client_id != self._client_id:
                    self._client_id = latest_client_id
                    # Paho keeps the connect payload's client id on the
                    # client object; keep it in sync before the retry.
                    try:
                        self._client._client_id = latest_client_id
                    except AttributeError:
                        pass
            _LOGGER.warning(
                "Leelen MQTT 已断开（%s），将按退避间隔重连",
                reason_code,
            )

    def _on_message(self, client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _LOGGER.debug("忽略无法解析的 Leelen MQTT 消息")
            return
        self._hass.loop.call_soon_threadsafe(
            self._coordinator.async_apply_mqtt_payload,
            payload,
        )

    def _dispatch_connection(self, connected):
        self._hass.loop.call_soon_threadsafe(
            self._coordinator.async_set_mqtt_connected,
            connected,
        )
