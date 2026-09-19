from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from time import monotonic, time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .device_catalog import (
    COLOR_TEMP_LIGHT_SERVICE_TYPES,
    CURTAIN_ANGLE_SERVICE_TYPES,
    DIMMABLE_LIGHT_SERVICE_TYPES,
    PLATFORM_SERVICE_TYPES,
    RGB_LIGHT_SERVICE_TYPES,
    SERVICE_TYPE_BREAKER_1PNL_3PNL,
    SERVICE_TYPE_BREAKER_1PN_3PN,
    SERVICE_TYPE_BREAKER_1P_3P,
    SERVICE_TYPE_CENTRAL_AIR_CONDITIONER,
    SERVICE_TYPE_FLOOR_HEATING,
    SERVICE_TYPE_FRESH_AIR,
    SERVICE_TYPE_FRESH_AIR_SERVER,
    SERVICE_TYPE_SENSOR,
    SERVICE_TYPE_TEMPERATURE_SENSOR,
    SERVICE_TYPE_TEMP_HUM_SENSOR,
    SERVICE_TYPE_SINGLE_AC_SERVER,
    SERVICE_TYPE_DOUBLE_AC_SERVER,
    SERVICE_TYPE_RELAY_SINGLE_AC_SERVER,
    SERVICE_TYPE_RELAY_FLOOR_SERVER,
    build_climate_sensor_sources,
)
from .leelen.api.protocol import pending_read_delay

_LOGGER = logging.getLogger(__name__)

FIID_CLIMATE = 49411
FIID_HEATER = 49415
FIID_TEMPERATURE = 16641
FIID_HUMIDITY = 16642
FIID_SWITCH = 49408
FIID_LAMP_LEVEL = 49228
FIID_LAMP_COLOR_TEMP = 49229
FIID_LAMP_RGB = 49230
FIID_CURTAIN_ACTION = 49409
FIID_CURTAIN_POSITION = 49410
FIID_CURTAIN_ANGLE = 49237
FIID_FRESH_AIR = 49412
FIID_BREAKER_SWITCH = 51201

_SERVICE_FIID_LISTS = {
    SERVICE_TYPE_CENTRAL_AIR_CONDITIONER: [FIID_CLIMATE, FIID_TEMPERATURE],
    SERVICE_TYPE_SINGLE_AC_SERVER: [FIID_CLIMATE, FIID_TEMPERATURE],
    SERVICE_TYPE_DOUBLE_AC_SERVER: [FIID_CLIMATE, FIID_TEMPERATURE],
    SERVICE_TYPE_RELAY_SINGLE_AC_SERVER: [FIID_CLIMATE, FIID_TEMPERATURE],
    SERVICE_TYPE_FLOOR_HEATING: [FIID_HEATER, FIID_TEMPERATURE],
    SERVICE_TYPE_RELAY_FLOOR_SERVER: [FIID_HEATER, FIID_TEMPERATURE],
    SERVICE_TYPE_FRESH_AIR: [FIID_FRESH_AIR],
    SERVICE_TYPE_FRESH_AIR_SERVER: [FIID_FRESH_AIR],
    SERVICE_TYPE_SENSOR: [FIID_TEMPERATURE, FIID_HUMIDITY],
    SERVICE_TYPE_TEMPERATURE_SENSOR: [FIID_TEMPERATURE, FIID_HUMIDITY],
    SERVICE_TYPE_TEMP_HUM_SENSOR: [FIID_TEMPERATURE, FIID_HUMIDITY],
    SERVICE_TYPE_BREAKER_1PNL_3PNL: [FIID_BREAKER_SWITCH],
    SERVICE_TYPE_BREAKER_1PN_3PN: [FIID_BREAKER_SWITCH],
    SERVICE_TYPE_BREAKER_1P_3P: [FIID_BREAKER_SWITCH],
}

for _service_type in PLATFORM_SERVICE_TYPES["light"]:
    _fiids = [FIID_SWITCH]
    if _service_type in DIMMABLE_LIGHT_SERVICE_TYPES:
        _fiids.append(FIID_LAMP_LEVEL)
    if _service_type in COLOR_TEMP_LIGHT_SERVICE_TYPES:
        _fiids.append(FIID_LAMP_COLOR_TEMP)
    if _service_type in RGB_LIGHT_SERVICE_TYPES:
        _fiids.append(FIID_LAMP_RGB)
    _SERVICE_FIID_LISTS[_service_type] = _fiids

for _service_type in PLATFORM_SERVICE_TYPES["cover"]:
    _fiids = [FIID_CURTAIN_ACTION, FIID_CURTAIN_POSITION]
    if _service_type in CURTAIN_ANGLE_SERVICE_TYPES:
        _fiids.append(FIID_CURTAIN_ANGLE)
    _SERVICE_FIID_LISTS[_service_type] = _fiids

for _service_type in PLATFORM_SERVICE_TYPES["switch"]:
    _SERVICE_FIID_LISTS.setdefault(_service_type, [FIID_SWITCH])

SERVICE_FIIDS = {
    service_type: tuple(fiids)
    for service_type, fiids in _SERVICE_FIID_LISTS.items()
}
SUPPORTED_FIIDS = {
    fiid for service_fiids in SERVICE_FIIDS.values() for fiid in service_fiids
}

REST_FALLBACK_INTERVAL = timedelta(seconds=30)
REST_PUSH_INTERVAL = timedelta(minutes=5)
CONTROL_PENDING_SECONDS = 60.0
CONTROL_CONFIRMED_GRACE_SECONDS = 20.0


class LeelenCoordinator(DataUpdateCoordinator):
    """Own device discovery, state merging, and control confirmation."""

    def __init__(self, hass: HomeAssistant, entry, api):
        super().__init__(
            hass,
            _LOGGER,
            name="leelen api",
            update_interval=REST_FALLBACK_INTERVAL,
            config_entry=entry,
        )
        self._entry = entry
        self._api = api
        self._data = {}
        self._control_expectations = {}
        self._mqtt_connected = False

    async def _async_update_data(self):
        _LOGGER.debug("=== 开始 REST 状态同步 ===")
        try:
            devices = self.get_devices()
            topology_changed = not devices
            if topology_changed:
                devices = await self._api.get_device_list_v2()
                devices = await self._api.get_online_status(devices)

            reads = self._build_state_reads(devices)
            state_response = (
                await self._api.read_dids_fiids_batch(reads)
                if reads
                else {"result": 1, "params": []}
            )
            states, state_times = self._state_index(state_response)
            sensor_sources = (
                build_climate_sensor_sources(devices)
                if topology_changed
                else self._data.get("climate_sensor_sources", {})
            )

            self._data = {
                "devices": devices,
                "states": states,
                "state_times": state_times,
                "climate_sensor_sources": sensor_sources,
                "mqtt_connected": self._mqtt_connected,
            }

            hass_data = self.hass.data.setdefault(DOMAIN, {})
            hass_data.setdefault("devices", {})[
                self._entry.entry_id
            ] = devices
            _LOGGER.debug(
                "=== REST 状态同步完成，%s 个设备%s ===",
                len(devices),
                "（已刷新拓扑）" if topology_changed else "",
            )
            return self._data
        except Exception as exc:
            _LOGGER.error("=== REST 状态同步失败: %s ===", exc)
            raise

    def async_set_mqtt_connected(self, connected):
        """Switch REST between disconnected fallback and push safety refresh."""
        connected = bool(connected)
        if self._mqtt_connected == connected:
            return
        self._mqtt_connected = connected
        self.update_interval = (
            REST_PUSH_INTERVAL if connected else REST_FALLBACK_INTERVAL
        )
        self._data["mqtt_connected"] = connected
        if self.data is not None:
            self.async_set_updated_data(self._data)
        _LOGGER.info(
            "Leelen MQTT %s，REST 同步间隔调整为 %s 秒",
            "已连接" if connected else "已断开",
            int(self.update_interval.total_seconds()),
        )

    def get_devices(self):
        return self._data.get("devices", [])

    def get_fiid_value(self, did, siid, fiid):
        return self._data.get("states", {}).get((did, siid, fiid))

    def get_climate_humidity(self, did, siid):
        source = self._data.get("climate_sensor_sources", {}).get(
            (did, siid)
        )
        if source is None:
            return None
        return self.get_fiid_value(*source, FIID_HUMIDITY)

    async def async_control_fiid(
        self,
        *,
        did,
        direct_did,
        siid,
        fiid,
        value,
    ):
        """Send one original-format control and merge device confirmation."""
        key = (did, siid, fiid)
        self._begin_control(key, value)
        try:
            result = await self._api.encrypt_v1_ctrl_fiids(
                did=did,
                direct_did=direct_did,
                siid=siid,
                fiids=[{"fiid": fiid, "value": value}],
            )
            if result.get("result") != 1:
                self._control_expectations.pop(key, None)
                return False

            retry_delay = pending_read_delay(result)
            if (
                retry_delay is not None
                and await self._wait_for_control(key, retry_delay)
            ):
                return True

            read_result = await self._api.read_dids_fiids(
                did=did,
                direct_did=direct_did,
                fiids=[fiid, FIID_TEMPERATURE],
                siid=siid,
                is_real_date=1,
            )
            return self._merge_control_read(read_result, key, value)
        except Exception:
            self._control_expectations.pop(key, None)
            raise

    def async_apply_mqtt_payload(self, payload):
        """Merge one original-format dmgr.notifyFIIDS push into HA state."""
        if not isinstance(payload, dict):
            return
        if payload.get("method") != "dmgr.notifyFIIDS":
            return
        params = payload.get("params")
        if not isinstance(params, dict):
            return

        did = params.get("did")
        siid = params.get("siid")
        if did is None or siid is None:
            return

        states = self._data.setdefault("states", {})
        state_times = self._data.setdefault("state_times", {})
        received_at = int(time() * 1000)
        changed = False
        for fiid_data in params.get("fiids") or []:
            fiid = fiid_data.get("fiid")
            if fiid not in SUPPORTED_FIIDS:
                continue
            changed = self._accept_state_value(
                states,
                state_times,
                (did, siid, fiid),
                fiid_data.get("value"),
                fiid_data.get("time", received_at),
            ) or changed

        if changed:
            self.async_set_updated_data(self._data)

    @staticmethod
    def _build_state_reads(devices):
        reads = []
        for device in devices:
            did = device.get("dev_addr")
            direct_did = device.get("direct_did")
            if not did or not direct_did:
                continue
            for service in device.get("logic_srv") or []:
                fiids = SERVICE_FIIDS.get(service.get("service_type"))
                siid = service.get("siid")
                if not fiids or siid is None:
                    continue
                reads.append(
                    {
                        "did": did,
                        "siid": siid,
                        "directDid": direct_did,
                        "fiids": list(fiids),
                        "isRealDate": 1,
                    }
                )
        return reads

    def _state_index(self, response):
        states = dict(self._data.get("states", {}))
        state_times = dict(self._data.get("state_times", {}))
        self._merge_state_response(response, states, state_times)
        return states, state_times

    def _merge_control_read(self, response, key, expected):
        if response.get("result") != 1:
            return False
        states = self._data.setdefault("states", {})
        state_times = self._data.setdefault("state_times", {})
        changed = self._merge_state_response(
            response,
            states,
            state_times,
        )
        confirmed = self._value_matches(states.get(key), expected)
        if changed:
            self.async_set_updated_data(self._data)
        return confirmed

    def _merge_state_response(self, response, states, state_times):
        if response.get("result") != 1:
            return False
        changed = False
        for item in response.get("params") or []:
            did = item.get("did")
            siid = item.get("siid")
            for fiid_data in item.get("fiids") or []:
                fiid = fiid_data.get("fiid")
                if did is None or siid is None or fiid is None:
                    continue
                changed = self._accept_state_value(
                    states,
                    state_times,
                    (did, siid, fiid),
                    fiid_data.get("value"),
                    fiid_data.get("time"),
                ) or changed
        return changed

    def _begin_control(self, key, expected):
        self._control_expectations[key] = {
            "expected": dict(expected),
            "confirmed": False,
            "deadline": monotonic() + CONTROL_PENDING_SECONDS,
            "event": asyncio.Event(),
        }

    async def _wait_for_control(self, key, timeout):
        expectation = self._control_expectations.get(key)
        if expectation is None:
            return False
        if expectation["confirmed"]:
            return True
        if timeout <= 0:
            return False
        try:
            await asyncio.wait_for(expectation["event"].wait(), timeout)
        except TimeoutError:
            return False
        return True

    def _accept_state_value(
        self,
        states,
        state_times,
        key,
        value,
        state_time,
    ):
        previous_time = state_times.get(key)
        if self._is_older(state_time, previous_time):
            return False

        previous_value = states.get(key)
        if isinstance(previous_value, dict) and isinstance(value, dict):
            value = {**previous_value, **value}

        expectation = self._control_expectations.get(key)
        now = monotonic()
        if expectation is not None:
            if self._value_matches(value, expectation["expected"]):
                if not expectation["confirmed"]:
                    expectation["confirmed"] = True
                    expectation["deadline"] = (
                        now + CONTROL_CONFIRMED_GRACE_SECONDS
                    )
                    expectation["event"].set()
            elif now < expectation["deadline"]:
                return False
            else:
                self._control_expectations.pop(key, None)

        changed = previous_value != value
        states[key] = value
        if state_time is not None:
            state_times[key] = state_time
        return changed

    @staticmethod
    def _value_matches(value, expected):
        return isinstance(value, dict) and all(
            value.get(field) == expected_value
            for field, expected_value in expected.items()
        )

    @staticmethod
    def _is_older(state_time, previous_time):
        if state_time is None or previous_time is None:
            return False
        try:
            return float(state_time) < float(previous_time)
        except (TypeError, ValueError):
            return False
