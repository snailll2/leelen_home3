import logging

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.components.climate.const import FAN_LOW,FAN_MEDIUM,FAN_HIGH
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .leelen.api.HttpApi import HttpApi

_LOGGER = logging.getLogger(__name__)

PRESET_MODES = {
    0: FAN_LOW,
    1: FAN_MEDIUM,
    2: FAN_HIGH,
}

# FAN_MODES = {
#     0: FAN_LOW,
#     1: FAN_MEDIUM,
#     2: FAN_HIGH,
# }

FIID_FRESHER = 49412
FIID_PM_25 = 16450
DEVICE_TYPE_FRESHER = 8217


SPEED_PERCENTAGE = {
    0: 33,
    1: 66,
    2: 100,
}

REVERSE_SPEED = {v: k for k, v in SPEED_PERCENTAGE.items()}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    devices = hass.data[DOMAIN].get('devices', {}).get(entry.entry_id, [])
    entities = []

    for device in devices:
        direct_did = device.get("direct_did")
        device_type = device.get("device_type")

        if device_type in [DEVICE_TYPE_FRESHER]:
            for logic_srv in device.get("logic_srv", []):
                siid = logic_srv.get("siid")
                entities.append(LeelenAirFresher(hass, entry, device, logic_srv, siid, direct_did))

    async_add_entities(entities)


class LeelenFan(FanEntity): 
    _attr_supported_features = FanEntityFeature.SET_SPEED   | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
    _attr_preset_modes = list(PRESET_MODES.values())
    _attr_speed_count = 3
    _attr_fiids = [FIID_FRESHER, FIID_PM_25]

    def __init__(self, hass, entry, device, logic_srv, siid, direct_did):
        self._hass = hass
        self._entry = entry
        self._device = device
        self._logic_srv = logic_srv
        self._did = device.get("dev_addr")
        self._direct_did = direct_did
        self._siid = siid
        self._device_type = device.get("device_type")

        self._name = logic_srv.get("logic_name", "Fan")

        self._is_on = False
        self._percentage = 0
        self._preset_mode = "auto"
        self._oscillating = False
        self._direction = None

        self._attr_unique_id = f"leelen_fan_{self._did}_{self._siid}"

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
    def percentage(self):
        return self._percentage

    @property
    def preset_mode(self):
        return self._preset_mode

    @property
    def oscillating(self):
        return self._oscillating

    @property
    def direction(self):
        return self._direction

    @property
    def speed_count(self) -> int:
        return self._attr_speed_count

    async def async_turn_on(self, percentage=None, preset_mode=None, **kwargs):
        self._is_on = True

        if percentage is not None:
            self._percentage = percentage
            for speed, pct in SPEED_PERCENTAGE.items():
                if pct >= percentage:
                    self._percentage = percentage
                    break

        if preset_mode is not None:
            self._preset_mode = preset_mode

        await self._send_control()

    async def async_turn_off(self, **kwargs):
        self._is_on = False
        await self._send_control()

    async def async_set_percentage(self, percentage: int):
        self._percentage = percentage
        if percentage > 0:
            self._is_on = True
        else:
            self._is_on = False
        await self._send_control()

    async def async_set_preset_mode(self, preset_mode: str):
        self._preset_mode = preset_mode
        await self._send_control()

    async def async_oscillate(self, oscillating: bool):
        self._oscillating = oscillating
        await self._send_control()

    async def async_set_direction(self, direction: str):
        self._direction = direction
        await self._send_control()
    @property
    def preset_modes(self) -> list[str] | None:
        """Return a list of available preset modes.

        Requires FanEntityFeature.SET_SPEED.
        """
        return self._attr_preset_modes


    async def _send_control(self):
        try:
            
            value = {"onOff": 1 if self._is_on else 0}

            if self._percentage > 0:
                speed = 1
                if self._percentage >= 66:
                    speed = 3
                elif self._percentage >= 33:
                    speed = 2
                value["windSpeed"] = speed
                self._is_on = True
            else:
                self._is_on = False

            
            

            # for mode_name, mode_value in PRESET_MODES.items():
            #     if mode_value == self._preset_mode:
            #         value["windSpeed"] = mode_name
            #         break

                    

            await HttpApi.get_instance(self._hass).encrypt_v1_ctrl_fiids(
                siid=self._siid,
                direct_did=self._direct_did,
                fiids=[{"fiid": FIID_FRESHER, "value": value}],
                did=self._did
            )
            _LOGGER.debug(f"控制风扇: {value}")

            import asyncio
            await asyncio.sleep(1.5)
            await self.async_update()
        except Exception as e:
            _LOGGER.error(f"控制风扇失败: {e}")

    async def async_update(self):
        try:
            result = await HttpApi.get_instance(self._hass).read_dids_fiids(
                did=self._did,
                direct_did=self._direct_did,
                fiids=[FIID_FRESHER],
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
                            wind_speed = value.get("windSpeed")
                            self._percentage = REVERSE_SPEED.get(wind_speed, 33)
                            self._preset_mode = PRESET_MODES.get(wind_speed, FAN_MEDIUM)
        except Exception as e:
            _LOGGER.error(f"更新风扇状态失败: {e}")

# 新风风扇  FiID_FRESHER = 49412
class LeelenAirFresher(LeelenFan): 
    _attr_name = "Leelen Air Fresher"
    _attr_device_type = DEVICE_TYPE_FRESHER


