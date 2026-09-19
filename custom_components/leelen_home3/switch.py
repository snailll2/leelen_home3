import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .coordinator import FIID_BREAKER_SWITCH, FIID_SWITCH
from .device_catalog import (
    SERVICE_TYPE_BREAKER_1PNL_3PNL,
    SERVICE_TYPE_BREAKER_1PN_3PN,
    SERVICE_TYPE_BREAKER_1P_3P,
    entity_unique_id,
    iter_platform_services,
)

_LOGGER = logging.getLogger(__name__)

BREAKER_SERVICE_TYPES = {
    SERVICE_TYPE_BREAKER_1PNL_3PNL,
    SERVICE_TYPE_BREAKER_1PN_3PN,
    SERVICE_TYPE_BREAKER_1P_3P,
}

# Breaker switch values are base64 uint16 byte streams (app:
# BreakerControlModel): "//8=" (0xFFFF) is on, "AAA=" (0x0000) is off.
BREAKER_ON = "//8="
BREAKER_OFF = "AAA="


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = []

    for device, logic_srv in iter_platform_services(
        coordinator.get_devices(),
        "switch",
    ):
        entity_class = (
            LeelenBreakerSwitch
            if logic_srv.get("service_type") in BREAKER_SERVICE_TYPES
            else LeelenSwitch
        )
        entities.append(entity_class(device, logic_srv, coordinator))
    async_add_entities(entities)


class LeelenSwitch(SwitchEntity):
    _attr_should_poll = False

    fiid = FIID_SWITCH

    def __init__(self, device, logic_srv, coordinator):
        self._device = device
        self._logic_srv = logic_srv
        self._coordinator = coordinator
        self._did = device.get("dev_addr")
        self._direct_did = device.get("direct_did")
        self._siid = logic_srv.get("siid")
        self._name = logic_srv.get("logic_name", "Switch")
        self._is_on = None

        self._attr_unique_id = entity_unique_id(device, logic_srv, "switch")
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

    def _apply_coordinator_state(self):
        value = self._coordinator.get_fiid_value(
            self._did,
            self._siid,
            self.fiid,
        )
        self._is_on = self._parse_value(value)

    def _parse_value(self, value):
        if isinstance(value, dict) and "onOff" in value:
            return value["onOff"] == 1
        return None

    async def _send_control(self, value):
        try:
            confirmed = await self._coordinator.async_control_fiid(
                did=self._did,
                direct_did=self._direct_did,
                siid=self._siid,
                fiid=self.fiid,
                value=value,
            )
            if not confirmed:
                _LOGGER.debug(
                    "设备尚未确认开关控制: did=%s siid=%s",
                    self._did,
                    self._siid,
                )
        except Exception as exc:
            _LOGGER.error("控制开关设备失败: %s", exc)

    async def async_turn_on(self, **kwargs):
        await self._send_control({"onOff": 1})

    async def async_turn_off(self, **kwargs):
        await self._send_control({"onOff": 0})


class LeelenBreakerSwitch(LeelenSwitch):
    fiid = FIID_BREAKER_SWITCH

    def _parse_value(self, value):
        if isinstance(value, dict) and "data" in value:
            return value["data"] == BREAKER_ON
        return None

    async def async_turn_on(self, **kwargs):
        await self._send_control({"data": BREAKER_ON})

    async def async_turn_off(self, **kwargs):
        await self._send_control({"data": BREAKER_OFF})
