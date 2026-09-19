"""Normalize Leelen cloud devices into Home Assistant entity candidates."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from typing import Any


# Logical service types, named after the official app's
# IotDeviceServiceType constants (decompiled from com.leelen.community).
SERVICE_TYPE_CENTRAL_AIR_CONDITIONER = 8259  # TYPE_485_AC_SERVER
SERVICE_TYPE_SINGLE_AC_SERVER = 8264
SERVICE_TYPE_DOUBLE_AC_SERVER = 8266
SERVICE_TYPE_RELAY_SINGLE_AC_SERVER = 8284
SERVICE_TYPE_FRESH_AIR = 8261  # TYPE_485_FRESH_SERVER
SERVICE_TYPE_FRESH_AIR_SERVER = 8267  # TYPE_FRESH_SERVER
SERVICE_TYPE_FLOOR_HEATING = 8268  # TYPE_FLOOR_ACTUATOR_SERVER
SERVICE_TYPE_RELAY_FLOOR_SERVER = 8285
SERVICE_TYPE_SENSOR = 8272  # TYPE_TEMP_CONTROL_SENSOR_CONTROL
SERVICE_TYPE_TEMPERATURE_SENSOR = 8246  # TYPE_IOT_TEMPERATURE
SERVICE_TYPE_TEMP_HUM_SENSOR = 8289  # TYPE_TEMP_HUM_SENSOR_SERVER
SERVICE_TYPE_LIGHT = 8212  # TYPE_IOT_LIGHT
SERVICE_TYPE_SMART_SOCKET = 8243  # TYPE_IOT_SMART_SOCKET
SERVICE_TYPE_SINGLE_CHANNEL_DYNAMIC = 8270  # TYPE_SINGLE_CHANNEL_DYNAMIC_SERVER
SERVICE_TYPE_SOLENOID_VALVE = 8295  # TYPE_SINGLE_CHANNEL_SOLENOID_VALVE
SERVICE_TYPE_VALVE_SERVER = 8309  # TYPE_VALVE_SERVER
SERVICE_TYPE_AUDIBLE_ALARM = 8333  # TYPE_SINGLE_CHANNEL_AUDIBLE_ALARM
SERVICE_TYPE_WATER_PURIFIER = 8491  # TYPE_WATER_PURIFIER
SERVICE_TYPE_DIMMER_DOWN_LAMP = 8291  # TYPE_DIMMER_DOWN_LAMP_SERVER
SERVICE_TYPE_RGB_DIMMER_LAMP = 8292  # TYPE_RGB_DIMMER_LAMP_SERVER
SERVICE_TYPE_DIMMER_SPOT_LAMP = 8293  # TYPE_DIMMER_SPOT_LAMP_SERVER
SERVICE_TYPE_COLOR_TEMP_LAMP = 8305  # TYPE_0_10V_COLOR_TEMPERATURE_LAMP
SERVICE_TYPE_DIMMER_LAMP = 8306  # TYPE_0_10V_DIMMER_LAMP
SERVICE_TYPE_MAGNETIC_LAMP = 8314  # TYPE_MAGNETIC_LAMP
SERVICE_TYPE_SINGLE_CHANNEL_LAMPS = 8330  # TYPE_SINGLE_CHANNEL_LAMPS
SERVICE_TYPE_SLIDE_LAMP = 8456  # TYPE_SLIDE_LAMP
SERVICE_TYPE_DUAL_COLOR_LAMP = 8459  # TYPE_DUAL_COLOR_LAMP
SERVICE_TYPE_CURTAIN = 8232  # TYPE_IOT_CURTAIN
SERVICE_TYPE_CURTAIN_TUBULAR = 8294  # TYPE_IOT_CURTAIN_TUBULAR
SERVICE_TYPE_CURTAIN_LOUVER = 8317  # TYPE_IOT_CURTAIN_LOUVER
SERVICE_TYPE_CURTAIN_DREAM = 8320  # TYPE_IOT_CURTAIN_DREAM
SERVICE_TYPE_BREAKER_1PNL_3PNL = 8200  # BREAKER_1PNL_3PNL (0x2008)
SERVICE_TYPE_BREAKER_1PN_3PN = 8201  # BREAKER_1PN_3PN (0x2009)
SERVICE_TYPE_BREAKER_1P_3P = 8202  # BREAKER_1P_3P (0x200A)

# Lamp types that support the louver-angle FIID (百叶帘角度).
CURTAIN_ANGLE_SERVICE_TYPES = {SERVICE_TYPE_CURTAIN_LOUVER}

CLIMATE_SERVICE_TYPES = {
    SERVICE_TYPE_CENTRAL_AIR_CONDITIONER,
    SERVICE_TYPE_FLOOR_HEATING,
}

# Lamp types that support the brightness FIID (app: iotLampFiid).
DIMMABLE_LIGHT_SERVICE_TYPES = {
    SERVICE_TYPE_DIMMER_DOWN_LAMP,
    SERVICE_TYPE_RGB_DIMMER_LAMP,
    SERVICE_TYPE_DIMMER_SPOT_LAMP,
    SERVICE_TYPE_COLOR_TEMP_LAMP,
    SERVICE_TYPE_DIMMER_LAMP,
    SERVICE_TYPE_MAGNETIC_LAMP,
    SERVICE_TYPE_SLIDE_LAMP,
    SERVICE_TYPE_DUAL_COLOR_LAMP,
}
# Lamp types that support the color-temperature FIID.
COLOR_TEMP_LIGHT_SERVICE_TYPES = {
    SERVICE_TYPE_DIMMER_DOWN_LAMP,
    SERVICE_TYPE_RGB_DIMMER_LAMP,
    SERVICE_TYPE_DIMMER_SPOT_LAMP,
    SERVICE_TYPE_COLOR_TEMP_LAMP,
    SERVICE_TYPE_MAGNETIC_LAMP,
    SERVICE_TYPE_DUAL_COLOR_LAMP,
}
# Lamp types that support the RGB FIID.
RGB_LIGHT_SERVICE_TYPES = {
    SERVICE_TYPE_RGB_DIMMER_LAMP,
    SERVICE_TYPE_MAGNETIC_LAMP,
}

PLATFORM_SERVICE_TYPES = {
    "climate": {
        SERVICE_TYPE_CENTRAL_AIR_CONDITIONER,
        SERVICE_TYPE_FLOOR_HEATING,
    },
    "fan": {
        SERVICE_TYPE_FRESH_AIR,
        SERVICE_TYPE_FRESH_AIR_SERVER,
    },
    "sensor": {
        SERVICE_TYPE_SENSOR,
        SERVICE_TYPE_TEMPERATURE_SENSOR,
        SERVICE_TYPE_TEMP_HUM_SENSOR,
    },
    "light": {
        SERVICE_TYPE_LIGHT,
        SERVICE_TYPE_DIMMER_DOWN_LAMP,
        SERVICE_TYPE_RGB_DIMMER_LAMP,
        SERVICE_TYPE_DIMMER_SPOT_LAMP,
        SERVICE_TYPE_COLOR_TEMP_LAMP,
        SERVICE_TYPE_DIMMER_LAMP,
        SERVICE_TYPE_MAGNETIC_LAMP,
        SERVICE_TYPE_SINGLE_CHANNEL_LAMPS,
        SERVICE_TYPE_SLIDE_LAMP,
        SERVICE_TYPE_DUAL_COLOR_LAMP,
    },
    "switch": {
        SERVICE_TYPE_SMART_SOCKET,
        SERVICE_TYPE_SINGLE_CHANNEL_DYNAMIC,
        SERVICE_TYPE_SOLENOID_VALVE,
        SERVICE_TYPE_VALVE_SERVER,
        SERVICE_TYPE_AUDIBLE_ALARM,
        SERVICE_TYPE_WATER_PURIFIER,
        SERVICE_TYPE_BREAKER_1PNL_3PNL,
        SERVICE_TYPE_BREAKER_1PN_3PN,
        SERVICE_TYPE_BREAKER_1P_3P,
    },
    "cover": {
        SERVICE_TYPE_CURTAIN,
        SERVICE_TYPE_CURTAIN_TUBULAR,
        SERVICE_TYPE_CURTAIN_LOUVER,
        SERVICE_TYPE_CURTAIN_DREAM,
    },
}

TEMPERATURE_KEYS = (
    "temperature",
    "curTemp",
    "currentTemp",
    "currentTemperature",
    "temp",
)

HUMIDITY_KEYS = (
    "humidity",
    "relativeHumidity",
)

_ROOM_NAME_SUFFIXES = re.compile(
    r"(中央空调|空调|地暖|温湿度传感器|温湿度|传感器|温控面板|温控器|温控|面板)"
)
_ROOM_NAME_SEPARATORS = re.compile(r"[\s_\-（）()]+")


def _matching_detail(did: Any, detail_response: Mapping[str, Any]) -> Mapping[str, Any]:
    detail_items = detail_response.get("params") or []
    for detail in detail_items:
        physic_device = detail.get("physicDevice") or {}
        if physic_device.get("did") == did:
            return detail
    if len(detail_items) == 1:
        return detail_items[0]
    return {}


def normalize_device(
    physical_device: Mapping[str, Any],
    detail_response: Mapping[str, Any],
) -> dict[str, Any]:
    """Return one physical device with independently typed logical services."""
    did = physical_device.get("did")
    detail = _matching_detail(did, detail_response)
    logical_services = []

    for logical_device in detail.get("logicDevices") or []:
        siid = logical_device.get("siid")
        service_type = logical_device.get("serviceType")
        if siid is None or service_type is None:
            continue
        logical_services.append(
            {
                "service_id": f"{did}_{siid}",
                "siid": siid,
                "logic_name": logical_device.get("logicName") or "Unknown",
                "profile_id": logical_device.get("profileId"),
                "service_type": service_type,
                "service_name": logical_device.get("purposeTypeName") or "",
                "sub_group_id": logical_device.get("subGroupId"),
            }
        )

    return {
        "dev_addr": did,
        "dev_name": physical_device.get("name") or "Unknown",
        "dev_type": str(physical_device.get("profileId") or ""),
        "direct_did": physical_device.get("directDid"),
        "room_name": physical_device.get("roomName") or "",
        "profile_id": physical_device.get("profileId"),
        "device_type": physical_device.get("deviceType"),
        "model": physical_device.get("softModel") or "",
        "logic_srv": logical_services,
    }


def iter_platform_services(
    devices: Iterable[Mapping[str, Any]], platform: str
) -> Iterator[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Yield device/service pairs supported by a Home Assistant platform."""
    supported_types = PLATFORM_SERVICE_TYPES.get(platform, set())
    for device in devices:
        for service in device.get("logic_srv") or []:
            if service.get("service_type") in supported_types:
                yield device, service


def entity_unique_id(
    device: Mapping[str, Any], service: Mapping[str, Any], platform: str
) -> str:
    """Return the unique ID shared by discovery and registry reconciliation."""
    return f"leelen_{platform}_{device.get('dev_addr')}_{service.get('siid')}"


def build_climate_sensor_sources(devices):
    """Associate climate services with their room's panel sensor service."""
    sensors = []
    climates = []
    for device in devices:
        did = device.get("dev_addr")
        for service in device.get("logic_srv") or []:
            siid = service.get("siid")
            if did is None or siid is None:
                continue
            item = {
                "key": (did, siid),
                "name": _normalized_room_name(service.get("logic_name")),
                "group": service.get("sub_group_id"),
            }
            service_type = service.get("service_type")
            if service_type == SERVICE_TYPE_SENSOR:
                sensors.append(item)
            elif service_type in CLIMATE_SERVICE_TYPES:
                climates.append(item)

    sources = {}
    unresolved = []
    used_sensor_names = set()
    for climate in climates:
        candidates = _matching_sensors(climate, sensors)
        if not candidates:
            unresolved.append(climate)
            continue
        sensor = candidates[0]
        sources[climate["key"]] = sensor["key"]
        used_sensor_names.add(sensor["name"])

    # Some cloud responses return every physical panel in the currently
    # selected room. A unique unmatched room/panel pair is still unambiguous.
    unresolved_names = {
        item["name"] for item in unresolved if item["name"]
    }
    remaining_sensors = [
        item for item in sensors if item["name"] not in used_sensor_names
    ]
    remaining_names = {item["name"] for item in remaining_sensors}
    if len(unresolved_names) == 1 and len(remaining_names) == 1:
        source = remaining_sensors[0]["key"]
        for climate in unresolved:
            sources[climate["key"]] = source

    return sources


def _matching_sensors(climate, sensors):
    name = climate["name"]
    exact = [sensor for sensor in sensors if sensor["name"] == name]
    if exact:
        return exact

    partial = [
        sensor
        for sensor in sensors
        if name
        and sensor["name"]
        and (name in sensor["name"] or sensor["name"] in name)
    ]
    if partial:
        return partial

    group = climate["group"]
    if group is None:
        return []
    return [sensor for sensor in sensors if sensor["group"] == group]


def _normalized_room_name(name):
    value = _ROOM_NAME_SUFFIXES.sub("", str(name or ""))
    return _ROOM_NAME_SEPARATORS.sub("", value).casefold()


def extract_temperature(value: Any) -> float | None:
    """Extract a Celsius value from Leelen sensor FIID payloads."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, Mapping):
        return None
    for key in TEMPERATURE_KEYS:
        if key in value:
            return extract_temperature(value[key])
    return None


def extract_humidity(value: Any) -> float | None:
    """Extract a relative-humidity percentage from a sensor FIID payload."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, Mapping):
        return None
    for key in HUMIDITY_KEYS:
        if key in value:
            return extract_humidity(value[key])
    return None
