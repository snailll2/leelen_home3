"""Config flow for Leelen Home integration."""
from __future__ import annotations

import hashlib
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, OPTIONS_SELECT, CONF_PHONE, CONF_DEVICE_ADDR, OPTIONS_CONFIG
from .leelen.api.HttpApi import HttpApi
from .leelen.utils.LogUtils import LogUtils

_LOGGER = logging.getLogger(__name__)


class LeelenIntegrationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._phone: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            phone = user_input.get("phone", "").strip()

            if not (phone.isdigit() and len(phone) == 11):
                errors["phone"] = "invalid_phone"
                return self._show_user_form(errors)

            uid_md5 = hashlib.md5(phone.encode("utf-8")).hexdigest()
            await self.async_set_unique_id(uid_md5)
            self._abort_if_unique_id_configured()

            self._phone = phone

            try:
                data = await HttpApi.get_instance(self.hass).VerifyCode(self._phone)
                if data.get("result") == 10026:
                    errors["phone"] = "sms_rate_limit"
                else:
                    _LOGGER.info("验证码已发送到: %s", phone)
                    return await self.async_step_verify()
            except Exception as exc:
                _LOGGER.exception("发送验证码失败")
                errors["phone"] = str(exc)

        return self._show_user_form(errors)

    def _show_user_form(self, errors: dict[str, str]) -> FlowResult:
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("phone"): str,
            }),
            errors=errors,
            description_placeholders={"desc": "请输入您的手机号"},
        )

    async def async_step_verify(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input.get("code", "").strip()
            try:
                result = await HttpApi.get_instance(self.hass).code_login(code)
                if result:
                    result[CONF_PHONE] = self._phone
                    # device_addr = result.get(CONF_DEVICE_ADDR)
                    group_name = result.get("groupName", "我的家")
                    _LOGGER.info("登录成功: %s", self._phone)
                    return self.async_create_entry(
                        title=f"家庭组：{group_name}({self._phone})",
                        data=result,
                    )
                errors["code"] = "invalid_code"
            except Exception as exc:
                _LOGGER.exception("登录失败")
                errors["code"] = f"login_failed: {exc}"

        return self.async_show_form(
            step_id="verify",
            data_schema=vol.Schema({
                vol.Required("code"): str,
            }),
            errors=errors,
            description_placeholders={"desc": "输入短信验证码"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry_id = config_entry.entry_id
        self._config_entry = config_entry
        self._config = dict(config_entry.options.get(OPTIONS_CONFIG, {}))
        self._refresh_stats: dict[str, str] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["refresh"],
        )

    async def async_step_refresh(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        try:
            api = HttpApi.get_instance(self.hass)
            all_devices = await api.get_device_list_v2()
            all_devices = await api.get_online_status(all_devices)

            self.hass.data.setdefault(DOMAIN, {})
            self.hass.data[DOMAIN].setdefault("devices", {})
            self.hass.data[DOMAIN]["devices"][self._entry_id] = all_devices

            all_entities = {
                f"leelen_{device.get('dev_addr')}_{srv.get('fiid')}"
                for device in all_devices
                for srv in device.get("logic_srv", [])
            }

            current_device_ids = {str(device.get("dev_addr")) for device in all_devices}

            entity_registry = er.async_get(self.hass)
            device_registry = dr.async_get(self.hass)

            removed = 0
            for dev in list(device_registry.devices.values()):
                dev_entry_ids = getattr(dev, 'config_entries', set())
                if self._entry_id not in dev_entry_ids:
                    continue
                for identifier in dev.identifiers:
                    if identifier[0] == DOMAIN and str(identifier[1]) not in current_device_ids:
                        _LOGGER.info("移除设备 %s，因为已从云端删除", identifier[1])
                        device_registry.async_remove_device(dev.id)
                        removed += 1
                        break

            removed_entities = 0
            for entry in list(entity_registry.entities.values()):
                if entry.config_entry_id != self._entry_id:
                    continue
                unique_id = entry.unique_id
                if unique_id and unique_id.startswith("leelen_") and unique_id not in all_entities:
                    entity_registry.async_remove(entry.entity_id)
                    removed_entities += 1


            added = len(all_entities)
            self._refresh_stats = {
                "total": str(len(all_devices)),
                "added": str(added),
                "removed": str(removed),
                "removed_entities": str(removed_entities)
            }
            return await self.async_step_refresh_result()
        except Exception as exc:
            _LOGGER.exception("刷新设备失败")
            LogUtils.e(exc)
            errors["base"] = str(exc)

        return self.async_show_form(
            step_id="refresh",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    async def async_step_refresh_result(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="refresh_result",
            data_schema=vol.Schema({}),
            description_placeholders=self._refresh_stats,
        )