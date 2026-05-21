import logging

from homeassistant.components.light import LightEntity, LightEntityFeature, ColorMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .leelen.api.HttpApi import HttpApi

_LOGGER = logging.getLogger(__name__)

FIID_ON_OFF = 49411
FIID_READ = 49415


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    devices = hass.data[DOMAIN].get('devices', {}).get(entry.entry_id, [])
    entities = []

    for device in devices:
        direct_did = device.get("direct_did")
        device_type = device.get("device_type")

        if device_type == 8888888:
            for logic_srv in device.get("logic_srv", []):
                siid = logic_srv.get("siid")
                entities.append(LeelenLight(hass, entry, device, logic_srv, siid, direct_did))

    async_add_entities(entities)


class LeelenLight(LightEntity):
    _attr_supported_features = LightEntityFeature.EFFECT | LightEntityFeature.TRANSITION
    _attr_supported_color_modes = {ColorMode.ONOFF, ColorMode.BRIGHTNESS}

    def __init__(self, hass, entry, device, logic_srv, siid, direct_did):
        self._hass = hass
        self._entry = entry
        self._device = device
        self._logic_srv = logic_srv
        self._did = device.get("dev_addr")
        self._direct_did = direct_did
        self._siid = logic_srv.get("siid")
        self._device_type = device.get("device_type")

        self._name = logic_srv.get("logic_name", "Light")

        self._is_on = False
        self._brightness = 0
        self._attr_unique_id = f"leelen_light_{self._did}_{self._siid}"

    @property
    def name(self):
        return self._name

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._did)},
            name=self._device.get("dev_name", "Leelen Device"),
            manufacturer="Leelen",
            model=str(self._device.get("model")),
        )

    @property
    def is_on(self):
        return self._is_on

    @property
    def brightness(self):
        return self._brightness

    @property
    def color_mode(self):
        if self._is_on and self._brightness > 0:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    async def async_turn_on(self, **kwargs):
        self._is_on = True
        if kwargs.get("brightness") is not None:
            self._brightness = kwargs["brightness"]
        else:
            self._brightness = 255
        await self._send_control()

    async def async_turn_off(self, **kwargs):
        self._is_on = False
        await self._send_control()

    async def _send_control(self):
        try:
            if self._is_on:
                brightness_percent = int((self._brightness / 255.0) * 100)
                value = {"onOff": 1, "brightness": brightness_percent}
            else:
                value = {"onOff": 0}

            await HttpApi.get_instance(self._hass).encrypt_v1_ctrl_fiids(
                siid=self._siid,
                direct_did=self._direct_did,
                fiids=[{"fiid": FIID_ON_OFF, "value": value}],
                did=self._did
            )

            import asyncio
            await asyncio.sleep(1.5)
            await self.async_update()
        except Exception as e:
            _LOGGER.error(f"控制灯光失败: {e}")

    async def async_update(self):
        try:
            result = await HttpApi.get_instance(self._hass).read_dids_fiids(
                did=self._did,
                direct_did=self._direct_did,
                fiids=[FIID_READ],
                siid=self._siid
            )

            if result.get("result") == 1:
                params = result.get("params", [])
                if params:
                    fiids_data = params[0].get("fiids", [])
                    if fiids_data:
                        value = fiids_data[0].get("value", {})
                        if isinstance(value, dict):
                            self._is_on = value.get("onOff", 0) == 1
                            brightness_percent = value.get("brightness", 100)
                            self._brightness = int((brightness_percent / 100.0) * 255)
        except Exception as e:
            _LOGGER.error(f"更新灯光状态失败: {e}")