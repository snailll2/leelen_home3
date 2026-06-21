import logging

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import HVACMode, ClimateEntityFeature,FAN_LOW,FAN_MEDIUM,FAN_HIGH
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .leelen.api.HttpApi import HttpApi

_LOGGER = logging.getLogger(__name__)

FAN_MODES = {
    0: FAN_LOW,
    1: FAN_MEDIUM,
    2: FAN_HIGH,
}

FIID_CLIMATE = 49411
FIID_HEATER = 49415

HVAC_MODE_MAP = {
    0: HVACMode.HEAT,
    1: HVACMode.COOL,
    2: HVACMode.FAN_ONLY,
    3: HVACMode.DRY,
}
DEVICE_TYPE_CLIMATE = 8221
DEVICE_TYPE_HEATTER = 8218


REVERSE_HVAC_MODE_MAP = {v: k for k, v in HVAC_MODE_MAP.items()}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    devices = hass.data[DOMAIN].get('devices', {}).get(entry.entry_id, [])
    entities = []

    for device in devices:
        direct_did = device.get("direct_did")
        device_type = device.get("device_type")

        if device_type  in [DEVICE_TYPE_CLIMATE]:
            for logic_srv in device.get("logic_srv", []):
                siid = logic_srv.get("siid")
                entities.append(LeelenClimate(hass, entry, device, logic_srv, siid, direct_did))
        elif device_type == DEVICE_TYPE_HEATTER:
            for logic_srv in device.get("logic_srv", []):
                siid = logic_srv.get("siid")
                entities.append(LeelenHeater(hass, entry, device, logic_srv, siid, direct_did))
    async_add_entities(entities)



class LeelenClimate(ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 5
    _attr_max_temp = 35
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.FAN_ONLY, HVACMode.DRY, HVACMode.AUTO]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE  | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
    def __init__(self, hass, entry, device, logic_srv, siid, direct_did):
        self._hass = hass
        self._entry = entry
        self._device = device
        self._logic_srv = logic_srv
        self._did = device.get("dev_addr")
        self._direct_did = direct_did
        self._siid = siid
        self._name = logic_srv.get("logic_name", "Air Conditioner")
        self._device_type = device.get("device_type")
        self._current_temperature = None
        self._target_temperature = 26
        self._hvac_mode = HVACMode.OFF
        
        self._fan_mode = FAN_MEDIUM
        self._on_off = False

        self._attr_unique_id = f"leelen_climate_{self._did}_{self._siid}"

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
    def current_temperature(self):
        return self._current_temperature

    @property
    def target_temperature(self):
        return self._target_temperature

    @property
    def hvac_mode(self):
        return self._hvac_mode

    @property
    def hvac_modes(self):
        return self._attr_hvac_modes

    @property
    def fan_mode(self):
        return self._fan_mode

    @property
    def fan_modes(self):
        return list(FAN_MODES.values())

    async def async_set_temperature(self, **kwargs):
        if kwargs.get("temperature") is not None:
            self._target_temperature = kwargs["temperature"]
            await self._send_control()

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode == HVACMode.OFF:
            self._on_off = False
            self._hvac_mode = HVACMode.OFF
        else:
            self._on_off = True
            self._hvac_mode = hvac_mode

        await self._send_control()

    async def async_set_fan_mode(self, fan_mode):
        self._fan_mode = fan_mode
        await self._send_control()

    async def _send_control(self):
        try:
            mode = REVERSE_HVAC_MODE_MAP.get(self._hvac_mode, 0)
            wind_speed = None
            for k, v in FAN_MODES.items():
                if v == self._fan_mode:
                    wind_speed = k
                    break

            value = {
                "onOff": 1 if self._on_off else 0,
                "mode": mode,
                "setTemp": int(self._target_temperature),
            }

            if wind_speed is not None:
                value["windSpeed"] = wind_speed

            await HttpApi.get_instance(self._hass).encrypt_v1_ctrl_fiids(
                siid=self._siid,
                direct_did=self._direct_did,
                fiids=[{"fiid": FIID_CLIMATE if self._device_type == DEVICE_TYPE_CLIMATE else FIID_HEATER, "value": value}],
                did=self._did
            )

            import asyncio
            await asyncio.sleep(1.5)
            await self.async_update()
        except Exception as e:
            _LOGGER.error(f"控制空调失败: {e}")

    async def async_update(self):
        try:
            result = await HttpApi.get_instance(self._hass).read_dids_fiids(
                did=self._did,
                direct_did=self._direct_did,
                fiids=[FIID_CLIMATE if self._device_type == DEVICE_TYPE_CLIMATE else FIID_HEATER],
                siid=self._siid
            )
            if result.get("result") == 1:
                params = result.get("params", [])
                if params:
                    fiids_data = params[0].get("fiids", [])
                    if fiids_data:
                        value = fiids_data[0].get("value", {})
                        if isinstance(value, dict):
                            self._on_off = value.get("onOff", 0) == 1
                            self._target_temperature = value.get("setTemp", 26)
                            # self._current_temperature = value.get("curTemp", value.get("setTemp"))
                            if value.get("mode") is not None:
                                self._hvac_mode = HVAC_MODE_MAP.get(value.get("mode"), HVACMode.OFF)

                            if not self._on_off:
                                self._hvac_mode = HVACMode.OFF

                            wind_speed = value.get("windSpeed")
                            if wind_speed is not None:
                                self._fan_mode = FAN_MODES.get(wind_speed, FAN_MEDIUM)
        except Exception as e:
            _LOGGER.error(f"更新空调状态失败: {e}")


class LeelenHeater(LeelenClimate):
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE  | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
