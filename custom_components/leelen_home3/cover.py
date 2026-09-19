import logging

from homeassistant.components.cover import (
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .coordinator import (
    FIID_CURTAIN_ACTION,
    FIID_CURTAIN_ANGLE,
    FIID_CURTAIN_POSITION,
)
from .device_catalog import (
    CURTAIN_ANGLE_SERVICE_TYPES,
    entity_unique_id,
    iter_platform_services,
)

_LOGGER = logging.getLogger(__name__)

# Curtain action values (app: IotCurtainMotorViewModel).
ACTION_CLOSE = 0
ACTION_OPEN = 1
ACTION_STOP = 2


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = []

    for device, logic_srv in iter_platform_services(
        coordinator.get_devices(),
        "cover",
    ):
        entities.append(LeelenCurtain(device, logic_srv, coordinator))
    async_add_entities(entities)


class LeelenCurtain(CoverEntity):
    _attr_should_poll = False

    def __init__(self, device, logic_srv, coordinator):
        self._device = device
        self._logic_srv = logic_srv
        self._coordinator = coordinator
        self._did = device.get("dev_addr")
        self._direct_did = device.get("direct_did")
        self._siid = logic_srv.get("siid")
        self._service_type = logic_srv.get("service_type")
        self._name = logic_srv.get("logic_name", "Curtain")

        self._tilt = self._service_type in CURTAIN_ANGLE_SERVICE_TYPES
        features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )
        if self._tilt:
            features |= CoverEntityFeature.SET_TILT_POSITION
        self._attr_supported_features = features

        self._position = None
        self._tilt_position = None

        self._attr_unique_id = entity_unique_id(device, logic_srv, "cover")
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
    def current_cover_position(self):
        return self._position

    @property
    def current_cover_tilt_position(self):
        return self._tilt_position if self._tilt else None

    @property
    def is_closed(self):
        if self._position is None:
            return None
        return self._position == 0

    @property
    def available(self):
        return self._position is not None

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
        position = self._coordinator.get_fiid_value(
            self._did,
            self._siid,
            FIID_CURTAIN_POSITION,
        )
        if isinstance(position, dict) and "position" in position:
            self._position = position["position"]

        if self._tilt:
            angle = self._coordinator.get_fiid_value(
                self._did,
                self._siid,
                FIID_CURTAIN_ANGLE,
            )
            if isinstance(angle, dict) and "curtainAngle" in angle:
                self._tilt_position = angle["curtainAngle"]

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
                    "设备尚未确认窗帘控制: did=%s siid=%s",
                    self._did,
                    self._siid,
                )
        except Exception as exc:
            _LOGGER.error("控制窗帘设备失败: %s", exc)

    async def async_open_cover(self, **kwargs):
        await self._send_control(FIID_CURTAIN_ACTION, {"action": ACTION_OPEN})

    async def async_close_cover(self, **kwargs):
        await self._send_control(FIID_CURTAIN_ACTION, {"action": ACTION_CLOSE})

    async def async_stop_cover(self, **kwargs):
        await self._send_control(FIID_CURTAIN_ACTION, {"action": ACTION_STOP})

    async def async_set_cover_position(self, **kwargs):
        position = kwargs.get("position")
        if position is None:
            return
        await self._send_control(
            FIID_CURTAIN_POSITION,
            {"position": int(max(0, min(100, position)))},
        )

    async def async_set_cover_tilt_position(self, **kwargs):
        tilt = kwargs.get("tilt")
        if tilt is None:
            return
        await self._send_control(
            FIID_CURTAIN_ANGLE,
            {"curtainAngle": int(max(0, min(100, tilt)))},
        )
