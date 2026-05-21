from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)

from .const import DOMAIN
from .leelen.api.HttpApi import HttpApi

_LOGGER = logging.getLogger(__name__)


class LeelenCoordinator(DataUpdateCoordinator):

    def __init__(self, hass: HomeAssistant, entry):
        super().__init__(
            hass,
            _LOGGER,
            name="leelen api",
            update_interval=timedelta(seconds=30),
        )
        self._entry = entry
        self._device_addr = entry.data.get("deviceAddr")
        self._data = {}

    async def _async_update_data(self):
        try:
            api = HttpApi.get_instance(self.hass)
            devices = await api.get_device_list_v2()
            devices = await api.get_online_status(devices)

            hass_data = self.hass.data.setdefault(DOMAIN, {})
            hass_data.setdefault('devices', {})[self._entry.entry_id] = devices

            self._data = {"devices": devices}

            for device in devices:
                for logic_srv in device.get("logic_srv", []):
                    did = device.get("dev_addr")
                    fiid = logic_srv.get("fiid")
                    siid = device.get("siid")
                    direct_did = device.get("direct_did")

                    if not all([did, fiid, siid, direct_did]):
                        continue

                    try:
                        result = await api.read_dids_fiids(
                            did=did,
                            direct_did=direct_did,
                            fiids=[fiid],
                            siid=siid
                        )

                        if result.get("result") == 1:
                            params = result.get("params", [])
                            if params:
                                fiids_data = params[0].get("fiids", [])
                                if fiids_data:
                                    value = fiids_data[0].get("value")
                                    key = f"{did}_{fiid}"
                                    self._data[key] = value
                    except Exception as e:
                        _LOGGER.error(f"更新设备 {did} 状态失败: {e}")

            return self._data
        except Exception as e:
            _LOGGER.error(f"更新数据失败: {e}")
            raise

    def get_device_state(self, did, fiid):
        return self._data.get(f"{did}_{fiid}")

    def get_devices(self):
        return self._data.get("devices", [])