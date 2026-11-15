import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_DEVICE_ADDR,
    CONF_GATEWAY_IP,
    CONF_GROUP_ID,
    CONF_MQTT_CLIENT_ID,
    CONF_MQTT_USERNAME,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
    DOMAIN,
    SUPPORTED_PLATFORMS,
)
from .coordinator import LeelenCoordinator
from .leelen.api.HttpApi import HttpApi
from .leelen.lan.manager import LeelenLanManager
from .leelen.utils.LogUtils import LogUtils
from .mqtt_client import LeelenMqttClient, build_mqtt_username

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # 老配置没有 refreshToken，提示用户删除重新添加
    if not entry.data.get(CONF_REFRESH_TOKEN):
        raise ConfigEntryNotReady(
            "配置已过期，缺少 refreshToken。请删除此集成后重新添加（设置 → 设备与服务 → 集成 → 立林3.0 → 删除）"
        )
    hass.data.setdefault(DOMAIN, {})
    LogUtils.d(__name__, f"开始设置集成, domain={DOMAIN}")

    hass.data[DOMAIN].setdefault('devices', {})
    hass.data[DOMAIN].setdefault('entities', {})

    for platform in SUPPORTED_PLATFORMS:
        hass.data[DOMAIN]['entities'][platform] = []

    api = HttpApi.get_instance(hass)
    api._entry_id = entry.entry_id
    api.device_addr = entry.data.get(CONF_DEVICE_ADDR, "")
    api.username = entry.data.get(CONF_USERNAME, "")
    api._access_token = entry.data.get(CONF_ACCESS_TOKEN, "")
    api._refresh_token = entry.data.get(CONF_REFRESH_TOKEN, "")
    api._token_expires_in = entry.data.get("expiresIn", 0)
    api._token_created_at = entry.data.get("tokenCreatedAt", 0)
    api._client_id = entry.data.get("mqttClientId", "")
    api._group_id = entry.data.get(CONF_GROUP_ID, "")
    # 设备标识跨重启保持不变（官方 App 用持久化的设备唯一标识）
    api.ensure_terminal_id(entry.data.get("appTerminalId"))

    LogUtils.d(__name__, f"API实例: {api}")
    LogUtils.d(__name__, f"api._group_id: {api._group_id if hasattr(api, '_group_id') else 'N/A'}")

    coordinator = LeelenCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    all_devices = coordinator.get_devices()

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "devices": all_devices,
        "options": dict(entry.options),
    }

    await hass.config_entries.async_forward_entry_setups(entry, SUPPORTED_PLATFORMS)
    LogUtils.d(__name__, f"平台设置完成: {SUPPORTED_PLATFORMS}")

    await _setup_lan(hass, entry, coordinator)

    mqtt_client_id = entry.options.get(CONF_MQTT_CLIENT_ID, "").strip()
    mqtt_username = entry.options.get(CONF_MQTT_USERNAME, "").strip()
    manual_mqtt = bool(mqtt_client_id and mqtt_username)
    if not manual_mqtt:
        mqtt_client_id, mqtt_username = await _resolve_auto_mqtt_credentials(
            api, entry
        )

    if mqtt_client_id and mqtt_username:
        mqtt_client = await hass.async_add_executor_job(
            LeelenMqttClient,
            hass,
            coordinator,
            api,
            mqtt_client_id,
            mqtt_username,
            not manual_mqtt,
        )
        hass.data[DOMAIN][entry.entry_id]["mqtt_client"] = mqtt_client
        await hass.async_add_executor_job(mqtt_client.start)
    else:
        _LOGGER.info("MQTT 凭据不可用，使用 REST 状态同步")

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _setup_lan(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: LeelenCoordinator
) -> None:
    """Bring up the LAN shortcut if a gateway is configured or discoverable.

    Two paths:
    - Manual: options flow stored a ``CONF_GATEWAY_IP`` → pin that gateway,
      skip mDNS discovery.
    - Auto: rely on ``_iot-gateway._tcp.local.`` mDNS announcements.

    The gateway ``did`` (needed as the ``dst`` of every iotlan handshake frame)
    is resolved from the cloud device list — look for the service whose
    ``service_type`` is 8211 (gateway) and read its ``dev_addr``.
    """
    # ``groupId`` is stored as a number on the cloud side, so config_entries
    # may persist it as an int; the LAN protocol, however, requires a string.
    group_id_raw = entry.data.get(CONF_GROUP_ID, "")
    group_id = str(group_id_raw) if group_id_raw else ""
    if not group_id:
        _LOGGER.info("缺少 groupId，跳过 Leelen LAN 通道")
        return

    gateway_did = _find_gateway_did(coordinator.get_devices())
    if not gateway_did:
        _LOGGER.info(
            "未在设备列表中找到网关 (service_type=8211)，跳过 Leelen LAN 通道"
        )
        return

    manual_ip = (entry.options.get(CONF_GATEWAY_IP) or "").strip()
    manager = LeelenLanManager(
        hass,
        group_id=group_id,
        on_push=_make_lan_push_callback(hass, coordinator),
        on_state=_make_lan_state_callback(hass, coordinator),
    )
    if manual_ip:
        manager.set_manual_gateway(manual_ip, did=gateway_did)
        _LOGGER.info(
            "Leelen LAN 使用手动网关 %s (did=%s)", manual_ip, gateway_did
        )
    else:
        # Pre-seed the discovered-gateway path with the cloud-known did so
        # mDNS only needs to fill in the IP/port.
        manager.set_known_gateway_did(gateway_did)
    coordinator.set_lan_manager(manager)
    hass.data[DOMAIN][entry.entry_id]["lan_manager"] = manager
    await manager.start()


def _find_gateway_did(devices: list) -> str | None:
    """Locate the gateway's ``dev_addr`` (service_type=8211) in the cloud list.

    Falls back to the most common ``direct_did`` across sub-devices when no
    explicit gateway service is present — every sub-device points back to its
    parent gateway via ``direct_did``.
    """
    gateway_service_type = 8211
    direct_did_counts: dict[str, int] = {}
    service_type_counts: dict[int, int] = {}
    for device in devices or []:
        for service in device.get("logic_srv") or []:
            st = service.get("service_type")
            if st is not None:
                service_type_counts[st] = service_type_counts.get(st, 0) + 1
            if st == gateway_service_type:
                dev_addr = device.get("dev_addr")
                if dev_addr:
                    _LOGGER.info(
                        "[lan-setup] 找到网关设备 service_type=8211 dev_addr=%s",
                        dev_addr,
                    )
                    return str(dev_addr)
        direct_did = device.get("direct_did")
        if direct_did:
            direct_did_counts[str(direct_did)] = (
                direct_did_counts.get(str(direct_did), 0) + 1
            )
    _LOGGER.debug(
        "[lan-setup] 设备列表未直接包含 service_type=8211; "
        "service_type 分布=%s direct_did 候选=%s",
        service_type_counts, direct_did_counts,
    )
    if direct_did_counts:
        picked = max(direct_did_counts, key=direct_did_counts.get)
        _LOGGER.info(
            "[lan-setup] 通过 direct_did 推断网关 did=%s (出现 %s 次)",
            picked, direct_did_counts[picked],
        )
        return picked
    _LOGGER.warning(
        "[lan-setup] 设备列表 %s 项，但找不到网关 did", len(devices or [])
    )
    return None


def _make_lan_push_callback(hass: HomeAssistant, coordinator: LeelenCoordinator):
    """Push the LAN payload into the coordinator.

    ``LeelenLanManager`` invokes this from inside HA's event loop, so a plain
    call is enough — no ``call_soon_threadsafe`` hop needed.
    """
    def _on_push(payload):
        coordinator.async_apply_lan_payload(payload)
    return _on_push


def _make_lan_state_callback(hass: HomeAssistant, coordinator: LeelenCoordinator):
    """Mirror LAN readiness into the coordinator."""
    def _on_state(state):
        coordinator.async_set_lan_connected(state == "ready")
    return _on_state


async def _resolve_auto_mqtt_credentials(api: HttpApi, entry: ConfigEntry):
    """Derive MQTT credentials the same way the official app does.

    官方 App（TokenLoader）连接 MQTT 时：username 为 "a5e4x84a:" +
    accountId，密码为 accessToken，clientId 取自 refreshToken 接口响应。
    """
    account_id = str(entry.data.get(CONF_ACCOUNT_ID) or "").strip()
    client_id = str(getattr(api, "_client_id", "") or "").strip()

    if account_id and not client_id:
        # 首次启动还没有 clientId：强制刷新一次 token，响应里会下发
        try:
            await api._do_refresh_token()
        except ConfigEntryAuthFailed:
            raise
        except Exception as exc:
            LogUtils.d(__name__, f"自动获取 MQTT clientId 失败: {exc}")
        client_id = str(getattr(api, "_client_id", "") or "").strip()

    if account_id and client_id:
        _LOGGER.info("已按官方 App 方式自动获取 MQTT 凭据")
        return client_id, build_mqtt_username(account_id)

    _LOGGER.info(
        "缺少 accountId 或 clientId，无法自动获取 MQTT 凭据，使用 REST 同步"
    )
    return "", ""


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    mqtt_client = runtime_data.get("mqtt_client")
    if mqtt_client:
        await hass.async_add_executor_job(mqtt_client.stop)

    lan_manager = runtime_data.get("lan_manager")
    if lan_manager is not None:
        await lan_manager.stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, SUPPORTED_PLATFORMS)

    if DOMAIN not in hass.data:
        return unload_ok

    if entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN].pop(entry.entry_id)
        LogUtils.d(__name__, f"已卸载配置项: {entry.entry_id}")

    if entry.entry_id in hass.data[DOMAIN].get('devices', {}):
        hass.data[DOMAIN]['devices'].pop(entry.entry_id)

    if not hass.data[DOMAIN].get('devices', {}):
        hass.data.pop(DOMAIN, None)

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Reload only when integration options changed, not on token refresh."""
    runtime_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime_data is None:
        return
    if runtime_data.get("options") != dict(entry.options):
        await hass.config_entries.async_reload(entry.entry_id)
