import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .device_catalog import (
    entity_unique_id,
    extract_humidity,
    extract_temperature,
    iter_platform_services,
)

_LOGGER = logging.getLogger(__name__)
FIID_TEMPERATURE = 16641
FIID_HUMIDITY = 16642


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    devices = hass.data[DOMAIN].get('devices', {}).get(entry.entry_id, [])
    coordinator = (
        hass.data[DOMAIN].get(entry.entry_id, {}).get("coordinator")
    )
    entities = []

    for device, logic_srv in iter_platform_services(devices, "sensor"):
        entities.extend(
            (
                LeelenPanelSensor(
                    entry,
                    device,
                    logic_srv,
                    coordinator,
                    FIID_TEMPERATURE,
                ),
                LeelenPanelSensor(
                    entry,
                    device,
                    logic_srv,
                    coordinator,
                    FIID_HUMIDITY,
                ),
            )
        )

    async_add_entities(entities)


class LeelenPanelSensor(SensorEntity):
    """A temperature or humidity reading from one thermostat panel."""

    _attr_should_poll = False

    def __init__(self, entry, device, logic_srv, coordinator, fiid):
        self._entry = entry
        self._device = device
        self._logic_srv = logic_srv
        self._did = device.get("dev_addr")
        self._siid = logic_srv.get("siid")
        self._coordinator = coordinator
        self._fiid = fiid

        base_name = logic_srv.get("logic_name", "Panel")
        if fiid == FIID_TEMPERATURE:
            self._name = f"{base_name} 温度"
            self._attr_unique_id = entity_unique_id(device, logic_srv, "sensor")
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_icon = "mdi:thermometer"
        else:
            self._name = f"{base_name} 湿度"
            self._attr_unique_id = (
                f"{entity_unique_id(device, logic_srv, 'sensor')}_humidity"
            )
            self._attr_device_class = getattr(
                SensorDeviceClass, "HUMIDITY", "humidity"
            )
            self._attr_native_unit_of_measurement = "%"
            self._attr_icon = "mdi:water-percent"

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
        if self._coordinator is None:
            return None
        value = self._coordinator.get_fiid_value(
            self._did,
            self._siid,
            self._fiid,
        )
        if self._fiid == FIID_TEMPERATURE:
            return extract_temperature(value)
        return extract_humidity(value)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if self._coordinator is not None:
            self.async_on_remove(
                self._coordinator.async_add_listener(
                    self._handle_coordinator_update
                )
            )

    def _handle_coordinator_update(self):
        self.async_write_ha_state()
