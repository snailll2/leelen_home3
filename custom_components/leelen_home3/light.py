import logging

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .coordinator import (
    FIID_LAMP_COLOR_TEMP,
    FIID_LAMP_LEVEL,
    FIID_LAMP_RGB,
    FIID_SWITCH,
)
from .device_catalog import (
    COLOR_TEMP_LIGHT_SERVICE_TYPES,
    DIMMABLE_LIGHT_SERVICE_TYPES,
    RGB_LIGHT_SERVICE_TYPES,
    entity_unique_id,
    iter_platform_services,
)

_LOGGER = logging.getLogger(__name__)

MIN_COLOR_TEMP_KELVIN = 2700
MAX_COLOR_TEMP_KELVIN = 6500


def _level_to_ha_brightness(level):
    return round(max(0, min(100, int(level))) * 255 / 100)


def _ha_brightness_to_level(brightness):
    return round(max(0, min(255, int(brightness))) * 100 / 255)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = []

    for device, logic_srv in iter_platform_services(
        coordinator.get_devices(),
        "light",
    ):
        entities.append(LeelenLight(device, logic_srv, coordinator))
    async_add_entities(entities)


class LeelenLight(LightEntity):
    _attr_should_poll = False

    def __init__(self, device, logic_srv, coordinator):
        self._device = device
        self._logic_srv = logic_srv
        self._coordinator = coordinator
        self._did = device.get("dev_addr")
        self._direct_did = device.get("direct_did")
        self._siid = logic_srv.get("siid")
        self._service_type = logic_srv.get("service_type")
        self._name = logic_srv.get("logic_name", "Light")

        self._dimmable = self._service_type in DIMMABLE_LIGHT_SERVICE_TYPES
        self._color_temp = self._service_type in COLOR_TEMP_LIGHT_SERVICE_TYPES
        self._rgb = self._service_type in RGB_LIGHT_SERVICE_TYPES

        color_modes = {ColorMode.ONOFF}
        if self._rgb:
            color_modes.add(ColorMode.RGB)
        else:
            if self._color_temp:
                color_modes.add(ColorMode.COLOR_TEMP)
            if self._dimmable:
                color_modes.add(ColorMode.BRIGHTNESS)
        self._attr_supported_color_modes = color_modes
        if self._color_temp:
            self._attr_min_color_temp_kelvin = MIN_COLOR_TEMP_KELVIN
            self._attr_max_color_temp_kelvin = MAX_COLOR_TEMP_KELVIN

        self._is_on = None
        self._level = None
        self._color_temp_kelvin = None
        self._rgb_color = None

        self._attr_unique_id = entity_unique_id(device, logic_srv, "light")
        self._apply_coordinator_state()

    @property
    def name(self):
        return self._name

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._logic_srv["service_id"])},
            name=self._name,
            manufacturer="Leelen",
            model=str(self._device.get("model")),
        )

    @property
    def is_on(self):
        return self._is_on

    @property
    def available(self):
        return self._is_on is not None

    @property
    def brightness(self):
        if not self._dimmable:
            return None
        if self._level is None:
            return None
        return _level_to_ha_brightness(self._level)

    @property
    def color_temp_kelvin(self):
        return self._color_temp_kelvin

    @property
    def rgb_color(self):
        return self._rgb_color

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_listener(
                self._handle_coordinator_update
            )
        )

    def _handle_coordinator_update(self):
        self._apply_coordinator_state()
        self.async_write_ha_state()

    def _get_fiid_value(self, fiid):
        return self._coordinator.get_fiid_value(self._did, self._siid, fiid)

    def _apply_coordinator_state(self):
        on_off = self._get_fiid_value(FIID_SWITCH)
        if isinstance(on_off, dict) and "onOff" in on_off:
            self._is_on = on_off["onOff"] == 1

        if self._dimmable:
            level = self._get_fiid_value(FIID_LAMP_LEVEL)
            if isinstance(level, dict) and "level" in level:
                self._level = level["level"]

        if self._color_temp:
            temp = self._get_fiid_value(FIID_LAMP_COLOR_TEMP)
            if isinstance(temp, dict) and "colorTemperaTure" in temp:
                self._color_temp_kelvin = temp["colorTemperaTure"]

        if self._rgb:
            rgb = self._get_fiid_value(FIID_LAMP_RGB)
            if isinstance(rgb, dict):
                rgb_value = rgb.get("rgbValue")
                if isinstance(rgb_value, dict):
                    self._rgb_color = (
                        rgb_value.get("redValue", 0),
                        rgb_value.get("greenValue", 0),
                        rgb_value.get("blueValue", 0),
                    )

    async def _send_control(self, fiid, value):
        try:
            confirmed = await self._coordinator.async_control_fiid(
                did=self._did,
                direct_did=self._direct_did,
                siid=self._siid,
                fiid=fiid,
                value=value,
            )
            if not confirmed:
                _LOGGER.debug(
                    "设备尚未确认灯光控制: did=%s siid=%s fiid=%s",
                    self._did,
                    self._siid,
                    fiid,
                )
        except Exception as exc:
            _LOGGER.error("控制灯光设备失败: %s", exc)

    async def async_turn_on(self, **kwargs):
        if self._is_on is False or kwargs.get(ATTR_BRIGHTNESS) is not None:
            await self._send_control(FIID_SWITCH, {"onOff": 1})

        if self._dimmable and kwargs.get(ATTR_BRIGHTNESS) is not None:
            level = _ha_brightness_to_level(kwargs[ATTR_BRIGHTNESS])
            await self._send_control(FIID_LAMP_LEVEL, {"level": level})

        if self._color_temp and kwargs.get(ATTR_COLOR_TEMP_KELVIN) is not None:
            kelvin = int(kwargs[ATTR_COLOR_TEMP_KELVIN])
            await self._send_control(
                FIID_LAMP_COLOR_TEMP,
                {"colorTemperaTure": kelvin},
            )

        if self._rgb and kwargs.get(ATTR_RGB_COLOR) is not None:
            red, green, blue = kwargs[ATTR_RGB_COLOR]
            await self._send_control(
                FIID_LAMP_RGB,
                {
                    "rgbValue": {
                        "redValue": int(red),
                        "greenValue": int(green),
                        "blueValue": int(blue),
                    }
                },
            )

    async def async_turn_off(self, **kwargs):
        await self._send_control(FIID_SWITCH, {"onOff": 0})
