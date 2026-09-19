"""Platform-level discovery tests with a minimal Home Assistant API surface."""

from __future__ import annotations

import asyncio
from enum import Enum, IntFlag
import importlib
from pathlib import Path
import sys
import types
import unittest

from tests.test_device_catalog import LIVE_ACCOUNT_FIXTURE, load_catalog_module


INTEGRATION_PATH = (
    Path(__file__).parents[1] / "custom_components" / "leelen_home3"
)


def add_module(name):
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def _ha_entity_stub():
    """Base entity stub carrying HA's attribute passthroughs."""

    class StubEntity:
        def async_write_ha_state(self):
            pass

        @property
        def supported_features(self):
            return self._attr_supported_features

        @property
        def supported_color_modes(self):
            return self._attr_supported_color_modes

    return StubEntity


def install_home_assistant_stubs():
    homeassistant = add_module("homeassistant")
    homeassistant.__path__ = []
    components = add_module("homeassistant.components")
    components.__path__ = []

    climate = add_module("homeassistant.components.climate")
    climate.ClimateEntity = _ha_entity_stub()
    climate_const = add_module("homeassistant.components.climate.const")

    class HVACMode(Enum):
        OFF = "off"
        HEAT = "heat"
        COOL = "cool"
        FAN_ONLY = "fan_only"
        DRY = "dry"
        AUTO = "auto"

    class ClimateEntityFeature(IntFlag):
        TARGET_TEMPERATURE = 1
        FAN_MODE = 2
        TURN_OFF = 4
        TURN_ON = 8

    climate_const.HVACMode = HVACMode
    climate_const.ClimateEntityFeature = ClimateEntityFeature
    climate_const.FAN_LOW = "low"
    climate_const.FAN_MEDIUM = "medium"
    climate_const.FAN_HIGH = "high"

    fan = add_module("homeassistant.components.fan")
    fan.FanEntity = _ha_entity_stub()

    class FanEntityFeature(IntFlag):
        SET_SPEED = 1
        TURN_ON = 2
        TURN_OFF = 4

    fan.FanEntityFeature = FanEntityFeature

    sensor = add_module("homeassistant.components.sensor")
    sensor.SensorEntity = _ha_entity_stub()

    class SensorDeviceClass(Enum):
        TEMPERATURE = "temperature"

    sensor.SensorDeviceClass = SensorDeviceClass

    switch = add_module("homeassistant.components.switch")
    switch.SwitchEntity = _ha_entity_stub()

    light = add_module("homeassistant.components.light")

    class ColorMode(Enum):
        ONOFF = "onoff"
        BRIGHTNESS = "brightness"
        COLOR_TEMP = "color_temp"
        RGB = "rgb"

    light.LightEntity = _ha_entity_stub()
    light.ColorMode = ColorMode
    light.ATTR_BRIGHTNESS = "brightness"
    light.ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
    light.ATTR_RGB_COLOR = "rgb_color"

    cover = add_module("homeassistant.components.cover")
    cover.CoverEntity = _ha_entity_stub()

    class CoverEntityFeature(IntFlag):
        OPEN = 1
        CLOSE = 2
        STOP = 4
        SET_POSITION = 8
        SET_TILT_POSITION = 16

    cover.CoverEntityFeature = CoverEntityFeature

    config_entries = add_module("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    core = add_module("homeassistant.core")
    core.HomeAssistant = object
    const = add_module("homeassistant.const")

    class UnitOfTemperature:
        CELSIUS = "°C"

    const.UnitOfTemperature = UnitOfTemperature
    helpers = add_module("homeassistant.helpers")
    helpers.__path__ = []
    entity = add_module("homeassistant.helpers.entity")
    entity.DeviceInfo = dict
    update_coordinator = add_module("homeassistant.helpers.update_coordinator")
    update_coordinator.DataUpdateCoordinator = type(
        "DataUpdateCoordinator", (), {}
    )


class FakeCoordinator:
    """Minimal coordinator surface used by the entity modules."""

    def __init__(self, devices, states=None):
        self._devices = devices
        self.states = states or {}
        self.controls = []

    def get_devices(self):
        return self._devices

    def get_fiid_value(self, did, siid, fiid):
        return self.states.get((did, siid, fiid))

    def get_climate_humidity(self, did, siid):
        return None

    def async_add_listener(self, listener):
        return lambda: None

    async def async_control_fiid(self, *, did, direct_did, siid, fiid, value):
        self.controls.append(
            {
                "did": did,
                "direct_did": direct_did,
                "siid": siid,
                "fiid": fiid,
                "value": value,
            }
        )
        return True


def load_platforms():
    install_home_assistant_stubs()
    package_name = "platform_probe"
    package = add_module(package_name)
    package.__path__ = [str(INTEGRATION_PATH)]
    leelen = add_module(f"{package_name}.leelen")
    leelen.__path__ = [str(INTEGRATION_PATH / "leelen")]
    api = add_module(f"{package_name}.leelen.api")
    api.__path__ = [str(INTEGRATION_PATH / "leelen" / "api")]
    http_api = add_module(f"{package_name}.leelen.api.HttpApi")
    http_api.HttpApi = type(
        "HttpApi", (), {"get_instance": classmethod(lambda cls, hass=None: cls())}
    )
    return {
        name: importlib.import_module(f"{package_name}.{name}")
        for name in (
            "climate", "fan", "sensor", "switch", "light", "cover", "mqtt_client"
        )
    }


def build_runtime():
    catalog = load_catalog_module()
    devices = [
        catalog.normalize_device(physical, detail)
        for physical, detail in LIVE_ACCOUNT_FIXTURE
    ]
    platforms = load_platforms()
    coordinator = FakeCoordinator(devices)

    class Entry:
        entry_id = "entry-1"

    class Hass:
        data = {
            "leelen3": {
                "devices": {"entry-1": devices},
                "entry-1": {"coordinator": coordinator},
            }
        }

    return catalog, platforms, coordinator, Hass(), Entry()


def create_live_platform_entities():
    _, platforms, coordinator, hass, entry = build_runtime()
    created = {}
    for name in ("climate", "fan", "sensor", "switch", "light", "cover"):
        entities = []
        asyncio.run(platforms[name].async_setup_entry(hass, entry, entities.extend))
        created[name] = entities
    return created, coordinator


class PlatformSetupTests(unittest.TestCase):
    def test_mqtt_username_matches_official_app_scheme(self):
        platforms = load_platforms()

        self.assertEqual(
            "a5e4x84a:13800138000",
            platforms["mqtt_client"].build_mqtt_username("13800138000"),
        )

    def test_platforms_create_entities_from_logical_service_types(self):
        created, _ = create_live_platform_entities()

        self.assertEqual(11, len(created["climate"]))
        self.assertEqual(2, len(created["fan"]))
        self.assertEqual(14, len(created["sensor"]))
        self.assertEqual(2, len(created["light"]))
        self.assertEqual(2, len(created["switch"]))
        self.assertEqual(2, len(created["cover"]))
        self.assertEqual(
            ["LeelenClimate"] * 6 + ["LeelenHeater"] * 5,
            [type(entity).__name__ for entity in created["climate"]],
        )
        self.assertEqual(
            "次卧1中央空调",
            created["climate"][0].name,
        )

    def test_climate_zones_are_separate_devices_without_splitting_other_platforms(self):
        created, _ = create_live_platform_entities()

        climate_identifiers = {
            next(iter(entity.device_info["identifiers"]))
            for entity in created["climate"]
        }
        self.assertEqual(11, len(climate_identifiers))
        self.assertIn(("leelen3", "ac-module_2"), climate_identifiers)
        self.assertIn(("leelen3", "heating-module_2"), climate_identifiers)

        self.assertEqual(
            {("leelen3", "fresh-air-module")},
            created["fan"][0].device_info["identifiers"],
        )
        self.assertEqual(
            {("leelen3", "panel-1")},
            created["sensor"][0].device_info["identifiers"],
        )

    def test_floor_heater_reports_heat_when_on_without_mode_field(self):
        created, coordinator = create_live_platform_entities()
        climate_platform = load_platforms()["climate"]
        hvac_mode = climate_platform.HVACMode

        heater = next(
            entity
            for entity in created["climate"]
            if type(entity).__name__ == "LeelenHeater"
        )
        coordinator.states[("heating-module", 2, 49415)] = {
            "onOff": 1,
            "setTemp": 26,
        }
        heater._handle_coordinator_update()

        self.assertEqual(hvac_mode.HEAT, heater.hvac_mode)

    def test_light_capabilities_follow_service_type(self):
        created, coordinator = create_live_platform_entities()
        light_platform = load_platforms()["light"]
        color_mode = light_platform.ColorMode

        simple_light = next(
            entity for entity in created["light"] if entity.name == "客厅灯"
        )
        dimmable_light = next(
            entity for entity in created["light"] if entity.name == "客厅调光灯"
        )

        self.assertEqual({color_mode.ONOFF}, simple_light.supported_color_modes)
        self.assertEqual(
            {color_mode.ONOFF, color_mode.BRIGHTNESS, color_mode.COLOR_TEMP},
            dimmable_light.supported_color_modes,
        )

        coordinator.states[("lamp-module", 3, 49408)] = {"onOff": 1}
        coordinator.states[("lamp-module", 3, 49228)] = {"level": 50}
        coordinator.states[("lamp-module", 3, 49229)] = {"colorTemperaTure": 4000}
        dimmable_light._handle_coordinator_update()

        self.assertTrue(dimmable_light.is_on)
        self.assertEqual(128, dimmable_light.brightness)
        self.assertEqual(4000, dimmable_light.color_temp_kelvin)

    def test_light_turn_on_sends_switch_and_level_commands(self):
        created, coordinator = create_live_platform_entities()

        dimmable_light = next(
            entity for entity in created["light"] if entity.name == "客厅调光灯"
        )
        asyncio.run(dimmable_light.async_turn_on(brightness=128))

        by_fiid = {control["fiid"]: control["value"] for control in coordinator.controls}
        self.assertEqual({"onOff": 1}, by_fiid[49408])
        self.assertEqual({"level": 50}, by_fiid[49228])

    def test_switch_and_breaker_control_values(self):
        created, coordinator = create_live_platform_entities()
        switch_platform = load_platforms()["switch"]

        socket = next(entity for entity in created["switch"])
        asyncio.run(socket.async_turn_on())
        self.assertEqual(
            {"onOff": 1},
            coordinator.controls[-1]["value"],
        )
        self.assertEqual(49408, coordinator.controls[-1]["fiid"])

        coordinator.states[("breaker-1", 2, 51201)] = {"data": "//8="}
        breaker = switch_platform.LeelenBreakerSwitch(
            *next(
                (device, logic_srv)
                for device, logic_srv in load_catalog_module().iter_platform_services(
                    coordinator.get_devices(), "switch"
                )
                if logic_srv["service_type"] == 8200
            ),
            coordinator,
        )
        self.assertTrue(breaker.is_on)

        coordinator.states[("breaker-1", 2, 51201)] = {"data": "AAA="}
        breaker._handle_coordinator_update()
        self.assertFalse(breaker.is_on)

    def test_curtain_controls_map_to_action_and_position_fiids(self):
        created, coordinator = create_live_platform_entities()

        cover_platform = load_platforms()["cover"]
        curtain = next(
            entity for entity in created["cover"] if entity.name == "客厅窗帘"
        )
        louver = next(
            entity for entity in created["cover"] if entity.name == "主卧百叶帘"
        )

        asyncio.run(curtain.async_open_cover())
        self.assertEqual(49409, coordinator.controls[-1]["fiid"])
        self.assertEqual({"action": 1}, coordinator.controls[-1]["value"])

        asyncio.run(curtain.async_close_cover())
        self.assertEqual({"action": 0}, coordinator.controls[-1]["value"])

        asyncio.run(curtain.async_stop_cover())
        self.assertEqual({"action": 2}, coordinator.controls[-1]["value"])

        asyncio.run(curtain.async_set_cover_position(position=45))
        self.assertEqual(49410, coordinator.controls[-1]["fiid"])
        self.assertEqual({"position": 45}, coordinator.controls[-1]["value"])

        coordinator.states[("curtain-module", 2, 49410)] = {"position": 45}
        curtain._handle_coordinator_update()
        self.assertEqual(45, curtain.current_cover_position)
        self.assertFalse(curtain.is_closed)

        self.assertTrue(
            louver.supported_features
            & cover_platform.CoverEntityFeature.SET_TILT_POSITION
        )
        asyncio.run(louver.async_set_cover_tilt_position(tilt=60))
        self.assertEqual(49237, coordinator.controls[-1]["fiid"])
        self.assertEqual({"curtainAngle": 60}, coordinator.controls[-1]["value"])

    def test_fan_reads_gear_field_reported_by_devices(self):
        created, _ = create_live_platform_entities()
        fan_platform = load_platforms()["fan"]

        fresher = created["fan"][0]

        class FakeApi:
            async def read_dids_fiids(self, **kwargs):
                return {
                    "result": 1,
                    "params": [
                        {"fiids": [{"value": {"onOff": 1, "gear": 2}}]}
                    ],
                }

        fan_platform.HttpApi.get_instance = classmethod(
            lambda cls, hass=None: FakeApi()
        )
        asyncio.run(fresher.async_update())

        self.assertTrue(fresher.is_on)
        self.assertEqual(100, fresher.percentage)


if __name__ == "__main__":
    unittest.main()
