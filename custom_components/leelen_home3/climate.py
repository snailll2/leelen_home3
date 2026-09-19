import logging

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .device_catalog import (
    SERVICE_TYPE_CENTRAL_AIR_CONDITIONER,
    entity_unique_id,
    extract_humidity,
    extract_temperature,
    iter_platform_services,
)

_LOGGER = logging.getLogger(__name__)

FAN_MODES = {
    0: FAN_LOW,
    1: FAN_MEDIUM,
    2: FAN_HIGH,
}

FIID_CLIMATE = 49411
FIID_HEATER = 49415
FIID_CURRENT_TEMPERATURE = 16641

HVAC_MODE_MAP = {
    0: HVACMode.HEAT,
    1: HVACMode.COOL,
    2: HVACMode.FAN_ONLY,
    3: HVACMode.DRY,
}
REVERSE_HVAC_MODE_MAP = {v: k for k, v in HVAC_MODE_MAP.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
):
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime["coordinator"]
    entities = []

    for device, logic_srv in iter_platform_services(
        coordinator.get_devices(),
        "climate",
    ):
        entity_class = (
            LeelenClimate
            if logic_srv.get("service_type")
            == SERVICE_TYPE_CENTRAL_AIR_CONDITIONER
            else LeelenHeater
        )
        entities.append(entity_class(device, logic_srv, coordinator))
    async_add_entities(entities)


class LeelenClimate(ClimateEntity):
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 5
    _attr_max_temp = 35
    _attr_target_temperature_step = 1.0
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.FAN_ONLY,
        HVACMode.DRY,
    ]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )

    def __init__(self, device, logic_srv, coordinator):
        self._device = device
        self._logic_srv = logic_srv
        self._coordinator = coordinator
        self._did = device.get("dev_addr")
        self._direct_did = device.get("direct_did")
        self._siid = logic_srv.get("siid")
        self._name = logic_srv.get("logic_name", "Air Conditioner")
        self._service_type = logic_srv.get("service_type")
        self._fiid = (
            FIID_CLIMATE
            if self._service_type == SERVICE_TYPE_CENTRAL_AIR_CONDITIONER
            else FIID_HEATER
        )

        self._current_temperature = None
        self._current_humidity = None
        self._target_temperature = 26
        self._hvac_mode = HVACMode.OFF
        self._fan_mode = FAN_MEDIUM
        self._raw_wind_speed = None
        self._on_off = False

        self._attr_unique_id = entity_unique_id(
            device,
            logic_srv,
            "climate",
        )
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
    def current_temperature(self):
        return self._current_temperature

    @property
    def current_humidity(self):
        return self._current_humidity

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

    @property
    def extra_state_attributes(self):
        if self._service_type != SERVICE_TYPE_CENTRAL_AIR_CONDITIONER:
            return {}
        return {"leelen_wind_speed": self._raw_wind_speed}

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

    async def async_set_temperature(self, **kwargs):
        temperature = kwargs.get("temperature")
        if temperature is None:
            return
        if self._service_type == SERVICE_TYPE_CENTRAL_AIR_CONDITIONER:
            value = {
                "setTemp": int(temperature),
                "onOff": 1,
            }
        else:
            value = self._complete_control_value(
                target_temperature=temperature,
            )
        await self._send_control(value)

    async def async_set_hvac_mode(self, hvac_mode):
        if self._service_type == SERVICE_TYPE_CENTRAL_AIR_CONDITIONER:
            if hvac_mode == HVACMode.OFF:
                value = {"onOff": 0}
            else:
                value = {
                    "mode": REVERSE_HVAC_MODE_MAP[hvac_mode],
                    "onOff": 1,
                }
        else:
            value = self._complete_control_value(
                on_off=hvac_mode != HVACMode.OFF,
                hvac_mode=hvac_mode,
            )
        await self._send_control(value)

    async def async_set_fan_mode(self, fan_mode):
        wind_speed = next(
            value for value, name in FAN_MODES.items() if name == fan_mode
        )
        await self._send_control(
            {
                "windSpeed": wind_speed,
                "onOff": 1,
            }
        )

    def _complete_control_value(
        self,
        *,
        on_off=None,
        hvac_mode=None,
        target_temperature=None,
    ):
        """Keep the existing floor-heating request shape unchanged."""
        if on_off is None:
            on_off = self._on_off
        if hvac_mode is None:
            hvac_mode = self._hvac_mode
        if target_temperature is None:
            target_temperature = self._target_temperature

        value = {
            "onOff": 1 if on_off else 0,
            "mode": REVERSE_HVAC_MODE_MAP.get(hvac_mode, 0),
            "setTemp": int(target_temperature),
        }
        wind_speed = next(
            (
                speed
                for speed, name in FAN_MODES.items()
                if name == self._fan_mode
            ),
            None,
        )
        if wind_speed is not None:
            value["windSpeed"] = wind_speed
        return value

    async def _send_control(self, value):
        try:
            confirmed = await self._coordinator.async_control_fiid(
                did=self._did,
                direct_did=self._direct_did,
                siid=self._siid,
                fiid=self._fiid,
                value=value,
            )
            if not confirmed:
                _LOGGER.debug(
                    "设备尚未确认暖通控制: did=%s siid=%s",
                    self._did,
                    self._siid,
                )
        except Exception as exc:
            _LOGGER.error("控制暖通设备失败: %s", exc)

    def _apply_coordinator_state(self):
        values = {
            self._fiid: self._coordinator.get_fiid_value(
                self._did,
                self._siid,
                self._fiid,
            ),
            FIID_CURRENT_TEMPERATURE: (
                self._coordinator.get_fiid_value(
                    self._did,
                    self._siid,
                    FIID_CURRENT_TEMPERATURE,
                )
            ),
        }
        self._apply_values(values)
        current_humidity = extract_humidity(
            self._coordinator.get_climate_humidity(
                self._did,
                self._siid,
            )
        )
        if current_humidity is not None:
            self._current_humidity = current_humidity

    def _apply_values(self, values):
        value = values.get(self._fiid)
        if isinstance(value, dict):
            if "onOff" in value:
                self._on_off = value["onOff"] == 1
            if "setTemp" in value:
                self._target_temperature = value["setTemp"]
            if "mode" in value:
                self._hvac_mode = HVAC_MODE_MAP.get(
                    value["mode"],
                    HVACMode.OFF,
                )

            if not self._on_off:
                self._hvac_mode = HVACMode.OFF
            elif (
                self._service_type
                != SERVICE_TYPE_CENTRAL_AIR_CONDITIONER
            ):
                self._hvac_mode = HVACMode.HEAT

            if "windSpeed" in value:
                wind_speed = value["windSpeed"]
                self._raw_wind_speed = wind_speed
                self._fan_mode = FAN_MODES.get(wind_speed)

        current_temperature = extract_temperature(
            values.get(FIID_CURRENT_TEMPERATURE)
        )
        if current_temperature is not None:
            self._current_temperature = current_temperature


class LeelenHeater(LeelenClimate):
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
