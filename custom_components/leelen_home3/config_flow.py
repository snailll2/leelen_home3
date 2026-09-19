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

from .const import (
    CONF_MQTT_CLIENT_ID,
    CONF_MQTT_USERNAME,
    CONF_PHONE,
    DOMAIN,
    OPTIONS_CONFIG,
)
from .device_catalog import PLATFORM_SERVICE_TYPES, entity_unique_id, iter_platform_services
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
                    _LOGGER.info("验证码已发送")
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
                    _LOGGER.info("登录成功")
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

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """刷新令牌过期，触发重新认证。"""
        entry = self._get_reauth_entry()
        phone = entry.data.get(CONF_PHONE, "")
        if not phone:
            return self.async_abort(reason="reauth_no_phone")

        # HA reauth 初始调用会把 entry.data 作为 user_input 传入（含 accessToken）
        # 此时显示确认页；用户提交空表单后才是真正的确认操作
        if user_input is None or "accessToken" in user_input:
            return self.async_show_form(
                step_id="reauth",
                data_schema=vol.Schema({}),
                description_placeholders={"phone": phone},
            )

        # 用户点击确认，发送验证码
        try:
            data = await HttpApi.get_instance(self.hass).VerifyCode(phone)
            if data.get("result") == 10026:
                return self.async_show_form(
                    step_id="reauth",
                    data_schema=vol.Schema({}),
                    errors={"base": "sms_rate_limit"},
                    description_placeholders={"phone": phone},
                )
            _LOGGER.info("验证码已发送")
        except Exception as exc:
            _LOGGER.exception("发送验证码失败")
            return self.async_show_form(
                step_id="reauth",
                data_schema=vol.Schema({}),
                errors={"base": str(exc)},
                description_placeholders={"phone": phone},
            )

        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        phone = entry.data.get(CONF_PHONE, "")

        if user_input is not None:
            code = user_input.get("code", "").strip()
            try:
                result = await HttpApi.get_instance(self.hass).code_login(code)
                if result:
                    result[CONF_PHONE] = phone
                    # 更新配置数据，保留原有数据并覆盖新 token
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            **entry.data,
                            "accessToken": result.get("accessToken"),
                            "refreshToken": result.get("refreshToken"),
                            "mqttClientId": result.get("mqttClientId", ""),
                            "username": result.get("username"),
                            "password": result.get("password"),
                            "deviceAddr": result.get("deviceAddr"),
                            "accountId": result.get("accountId"),
                            "groupId": result.get("groupId"),
                            "groupName": result.get("groupName"),
                        },
                        title=f"家庭组：{result.get('groupName', '我的家')}({phone})",
                    )
                    LogUtils.d("config_flow", "重新认证成功，token 已更新")
                    return self.async_abort(reason="reauth_successful")
                errors["code"] = "invalid_code"
            except Exception as exc:
                _LOGGER.exception("重新认证失败")
                errors["code"] = f"reauth_failed: {exc}"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Required("code"): str,
            }),
            errors=errors,
            description_placeholders={"phone": phone},
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
            menu_options=["refresh", "mqtt"],
        )

    async def async_step_mqtt(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client_id = str(
                user_input.get(CONF_MQTT_CLIENT_ID) or ""
            ).strip()
            username = str(
                user_input.get(CONF_MQTT_USERNAME) or ""
            ).strip()
            if bool(client_id) != bool(username):
                errors["base"] = "mqtt_pair_required"
            else:
                options = dict(self._config_entry.options)
                if client_id:
                    options[CONF_MQTT_CLIENT_ID] = client_id
                    options[CONF_MQTT_USERNAME] = username
                else:
                    options.pop(CONF_MQTT_CLIENT_ID, None)
                    options.pop(CONF_MQTT_USERNAME, None)
                return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="mqtt",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_MQTT_CLIENT_ID,
                        default=self._config_entry.options.get(
                            CONF_MQTT_CLIENT_ID,
                            "",
                        ),
                    ): str,
                    vol.Optional(
                        CONF_MQTT_USERNAME,
                        default=self._config_entry.options.get(
                            CONF_MQTT_USERNAME,
                            "",
                        ),
                    ): str,
                }
            ),
            errors=errors,
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
                entity_unique_id(device, service, platform)
                for platform in PLATFORM_SERVICE_TYPES
                for device, service in iter_platform_services(all_devices, platform)
            }

            current_device_ids = {str(device.get("dev_addr")) for device in all_devices}

            entity_registry = er.async_get(self.hass)
            device_registry = dr.async_get(self.hass)
            current_entities = {
                registry_entry.unique_id
                for registry_entry in entity_registry.entities.values()
                if registry_entry.config_entry_id == self._entry_id
            }
            new_entities = all_entities - current_entities

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

            await self.hass.config_entries.async_reload(self._entry_id)

            self._refresh_stats = {
                "total": str(len(all_devices)),
                "added": str(len(new_entities)),
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
