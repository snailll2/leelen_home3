import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .leelen.api.HttpApi import HttpApi

_LOGGER = logging.getLogger(__name__)
FIID_READ = 49415



async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    devices = hass.data[DOMAIN].get('devices', {}).get(entry.entry_id, [])
    entities = []

    for device in devices:
        direct_did = device.get("direct_did")

        for logic_srv in device.get("logic_srv", []):
            siid = logic_srv.get("siid")
            params = logic_srv.get("params", [])

            for param in params:
                if param.get("dataType") in [1, 2, 3]:
                    entities.append(LeelenSensor(hass, entry, device, logic_srv, siid, direct_did, param))

    async_add_entities(entities)


class LeelenSensor(SensorEntity):

    def __init__(self, hass, entry, device, logic_srv, siid, direct_did, param_info):
        self._hass = hass
        self._entry = entry
        self._device = device
        self._logic_srv = logic_srv
        self._siid = siid
        self._direct_did = direct_did
        self._param_info = param_info
        self._did = device.get("dev_addr")

        property_name = param_info.get("propertyName", "unknown")
        self._name = f"{logic_srv.get('logic_name', '')} {property_name}"
        self._unique_id_suffix = f"{self._did}_{self._siid}_{param_info.get('propertyId', 'unknown')}"

        self._attr_unique_id = f"leelen_sensor_{self._unique_id_suffix}"
        self._attr_icon = "mdi:thermometer"
        self._attr_native_unit_of_measurement = param_info.get("unit")

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
    def native_value(self):
        state = self._state
        if state is not None:
            try:
                return float(state)
            except (ValueError, TypeError):
                return state
        return None

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
                            property_id = self._param_info.get("propertyId")
                            self._state = value.get(property_id)
        except Exception as e:
            _LOGGER.error(f"更新传感器状态失败: {e}")