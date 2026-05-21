import os
import hashlib
import json
import random
from re import S
import string
import threading
import time
import uuid
from typing import Any

import aiofiles as aiofiles
import aiohttp
import aiosqlite
from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..entity.BaseParam import BaseParam, CodeLoginRequestParam, GetVerifyCodeRequestParam
from ..entity.BaseRequest import BaseRequest
from ..utils.AesCoder import AesCoder
from ..utils.LogUtils import LogUtils
from ..utils.RSAEncrypt import RSAEncrypt


class HttpApi:
    _instance = None
    _lock = threading.Lock()

    def __init__(self, hass: HomeAssistant):
        self.BASE_URL = "https://iot.leelen.com"
        self.RD_BASE_URL = "https://rd.iot.leelen.com"
        self.device_addr = ""
        self.appTerminalId = f"ANDROID-{self.get_terminal_id()}"
        self.appTerminalModel = "REP-AN00"
        self.uuid = None
        self.verifyCodeSign = ""
        self.username = ""
        self._hass = hass
        self._device_list = []
        self._access_token = ""
        self._group_id = ""

    def get_secret(self, num: int) -> str:
        chars = string.ascii_letters + string.digits
        return ''.join(random.choice(chars) for _ in range(num))

    @classmethod
    def get_instance(cls, hass: HomeAssistant = None):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = HttpApi(hass)
        return cls._instance

    def get_terminal_id(self):
        return hashlib.md5(''.join(random.choices(string.ascii_letters + string.digits, k=32)).encode()).hexdigest()

    async def _make_request(self, url, params, seq, version="V1.0"):
        session = async_get_clientsession(self._hass)
        headers = {}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"

        async with session.post(
            url,
            verify_ssl=False,
            headers=headers,
            json={
                "params": params,
                "seq": seq,
                "version": version
            },
        ) as res:
            res.raise_for_status()
            res_dict = await res.json(encoding="utf-8")
            LogUtils.d("HttpApi", f"请求: {url} params={params} seq={seq} version={version} 返回: {res_dict}")
            return res_dict

    async def get_user(self, accessToken):
        session = async_get_clientsession(self._hass)
        headers = {
            "Authorization": f"Bearer {accessToken}"
        }
        async with session.post(
                f"{self.BASE_URL}/rest/app/community/platform/getUser",
                verify_ssl=False,
                headers=headers,
                json={},
        ) as res:
            res.raise_for_status()
            res_dict = await res.json(encoding="utf-8")
            return res_dict

    async def get_physics_device_list(self, groupId):
        url = f"{self.BASE_URL}/rest/app/community/smartHome/getPhysicsDeviceList"
        params = {
            "groupId": groupId
        }
        return await self._make_request(url, params, 85)

    async def get_device_list_v2(self):
        url = f"{self.BASE_URL}/rest/app/community/smartHome/getPhysicsDeviceList"
        params = {
            "groupId": self._group_id
        }
        LogUtils.d("HttpApi", f"getPhysicsDeviceList 请求: groupId={self._group_id}")
        result = await self._make_request(url, params, 100)
        LogUtils.d("HttpApi", f"getPhysicsDeviceList 返回: result={result.get('result')}")

        devices = []
        if result.get("result") == 1:
            params_data = result.get("params", [])
            LogUtils.d("HttpApi", f"物理设备数量: {len(params_data)}")
            for device_info in params_data:
                did = device_info.get("did")
                direct_did = device_info.get("directDid")
                name = device_info.get("name", "Unknown")
                room_name = device_info.get("roomName", "")
                profile_id = device_info.get("profileId")
                device_type = device_info.get("deviceType")
                model = device_info.get("softModel", "")

                logic_device = {
                    "dev_addr": did,
                    "dev_name": name,
                    "dev_type": str(profile_id) if profile_id else "",
                    "direct_did": direct_did,
                    "room_name": room_name,
                    "profile_id": profile_id,
                    "device_type": device_type,
                    "model": model,
                    "logic_srv": []
                }

                if did and direct_did:
                    try:
                        detail_result = await self.get_devices(
                            did=did,
                            direct_did=direct_did
                        )
                        if detail_result.get("result") == 1:
                            detail_params = detail_result.get("params", [])
                            if detail_params:
                                device_data = detail_params[0]
                                logic_devices = device_data.get("logicDevices", [])
                                for logic_dev in logic_devices:
                                    logic_device["logic_srv"].append({
                                        "siid": logic_dev.get("siid"),        # 原始 siid
                                        "fiid": logic_dev.get("siid"),        # 兼容现有代码
                                        "logic_name": logic_dev.get("logicName", ""),
                                        "profile_id": profile_id,
                                        "service_type": logic_dev.get("serviceType"),
                                        "service_name": logic_dev.get("purposeTypeName", "")
                                    })
                                
                        else:
                            LogUtils.d("HttpApi", f"设备 {did} getDevices 失败: result={detail_result.get('result')}")
                    except Exception as e:
                        LogUtils.e(f"获取设备 {did} 详情失败: {e}")

                devices.append(logic_device)
        else:
            LogUtils.d("HttpApi", f"getPhysicsDeviceList 返回失败: result={result.get('result')}")

        LogUtils.d("HttpApi", f"get_device_list_v2 返回设备数: {len(devices)}")
        return devices

    async def get_online_status(self, devices):
        if not devices:
            return devices
        
        params = []
        for device in devices:
            params.append({
                "did": device.get("dev_addr"),
                "directDid": device.get("direct_did")
            })
        
        try:
            result = await self._make_request(
                f"{self.BASE_URL}/rest/app/community/dc/batchGetDeviceOnlineInfo",
                params,
                101
            )
            
            if result.get("result") == 1:
                online_info_list = result.get("params", [])
                online_map = {}
                for info in online_info_list:
                    did = info.get("did")
                    online_map[did] = {
                        "isOnline": info.get("isOnline", 0),
                        "isDormancy": info.get("isDormancy", False)
                    }
                
                for device in devices:
                    did = device.get("dev_addr")
                    if did in online_map:
                        device["online_info"] = online_map[did]
        except Exception as e:
            LogUtils.e(f"获取在线状态失败: {e}")
        
        return devices

    async def control_device_fiids(self, accessToken, siid, directDid, fiids, did):
        session = async_get_clientsession(self._hass)
        headers = {
            "Authorization": f"Bearer {accessToken}"
        }
        async with session.post(
                f"{self.BASE_URL}/rest/app/community/encryptV1CtrlFIIDS",
                verify_ssl=False,
                headers=headers,
                json={
                    "params": {
                        "siid": siid,
                        "directDid": directDid,
                        "fiids": fiids,
                        "did": did
                    },
                    "seq": 74,
                    "version": "V1.1"
                },
        ) as res:
            res.raise_for_status()
            res_dict = await res.json(encoding="utf-8")
            return res_dict

    async def third_login(self, username, password):
        headers = {}
        data = {
            "appTerminalId": self.appTerminalId,
            "password": password,
            "appTerminalModel": self.appTerminalModel,
            "loginMark": "0",
            "osVersion": "12",
            "appTerminalName": "null",
            "osType": "1",
            "packageName": "com.leelen.luxdomo",
            "userName": username,
            "autoLogin": "0"
        }
        session = async_get_clientsession(self._hass)

        async with session.post(
                f"{self.RD_BASE_URL}/rest/api/third/app/user/login",
                verify_ssl=False,
                headers=headers,
                data=data,
        ) as res:
            res.raise_for_status()
            res_dict = await res.json(encoding="utf-8")
            return res_dict

    async def get_homes(self):
        url = f"{self.BASE_URL}/rest/app/community/mergeHomes/getHomes"
        return await self._make_request(url, {}, 151)

    async def get_device_list(self, userName):
        headers = {}
        data = {
            "userName": userName
        }
        session = async_get_clientsession(self._hass)

        async with session.post(
                f"{self.RD_BASE_URL}/rest/app/device/deviceList",
                verify_ssl=False,
                headers=headers,
                data=data,
        ) as res:
            res.raise_for_status()
            res_dict = await res.json(encoding="utf-8")
            return res_dict

    async def VerifyCode(self, username):
        params = GetVerifyCodeRequestParam(username=username)
        baseRequest = BaseRequest()
        baseRequest.params = params.to_dict()
        baseRequest.seq = 93
        LogUtils.d(json.dumps(baseRequest.to_dict()))
        session = async_get_clientsession(self._hass)
        async with session.post(
                f"{self.BASE_URL}/rest/app/community/security/getVerifyCode",
                verify_ssl=False,
                json=baseRequest.to_dict(),
        ) as res:
            res.raise_for_status()
            data = await res.json(encoding="utf-8")
            LogUtils.d(data)
            self.verifyCodeSign = data.get("params")
            self.username = username
            return data

    async def verifyCodeLogin(self, username=None, verifyCode=None, verifyCodeSign=None, publicKey=None):
        params = CodeLoginRequestParam()
        params.username = username
        params.Phone = username
        params.verifyCode = verifyCode
        params.verifyCodeSign = verifyCodeSign
        params.terminalId = self.appTerminalId
        LogUtils.d(json.dumps(params.to_dict()))

        params = self.encrypt_params(params.to_dict(), publicKey)
        baseRequest = BaseRequest()
        baseRequest.params = params.to_dict()
        baseRequest.seq = 93
        LogUtils.d(json.dumps(baseRequest.to_dict()))

        session = async_get_clientsession(self._hass)

        async with session.post(
                f"{self.BASE_URL}/rest/app/community/user/verifyCodeLogin",
                verify_ssl=False,
                json=baseRequest.to_dict(),
        ) as res:
            res.raise_for_status()
            data = await res.json(encoding="utf-8")
            LogUtils.d(baseRequest.to_dict())
            LogUtils.d(data)
            if data["result"] != 1:
                raise Exception(data["message"])
            self.verifyCodeSign = data.get("params")
            self.username = username
            return data

    async def code_login(self, verifyCode):
        self.uuid = await self.get_uuid()
        code_login_result = await self.verifyCodeLogin(self.username, verifyCode, self.verifyCodeSign, self.uuid)
        accessToken = code_login_result.get("params", {}).get("accessToken")
        self._access_token = accessToken
        user_data = await self.get_user(accessToken)
        username = user_data.get("params", {}).get("userName")
        password = user_data.get("params", {}).get("password")

        third_result = await self.third_login(username, password)
        bindCallers = third_result.get("bindCallers")
        accountId = third_result.get("accountId")
        LogUtils.d("third_result", third_result)
        group_name  = "我的家"
        homes_result = await self.get_homes()
        homes = homes_result.get("params", [])
        if len(homes) > 0:
            self._group_id = homes[0].get("groupId")
            group_name = homes[0].get("groupName")
        elif len(bindCallers) > 0:
            self._group_id = bindCallers[0].get("groupId")
            group_name = bindCallers[0].get("groupName")
        else:
            raise Exception("未找到有效的家庭组")

        if len(bindCallers) > 0:
            deviceAddr = bindCallers[0].get("deviceAddr")
        else:
            deviceAddr = None

        return {
            "username": username,
            "password": password,
            "deviceAddr": deviceAddr,
            "accountId": accountId,
            "accessToken": accessToken,
            "groupId": self._group_id,
            "groupName": group_name
        }

    async def get_uuid(self):
        session = async_get_clientsession(self._hass)
        async with session.post(
                f"{self.BASE_URL}/rest/app/community/safe/getUuid",
                verify_ssl=False,
                json={},
        ) as res:
            res.raise_for_status()
            data = await res.json(encoding="utf-8")
            LogUtils.d(data)
            self.uuid = data.get("params", {}).get("uuid")
            return data.get("params", {}).get("uuid")

    def encrypt_params(self, obj: Any, public_key: str) -> 'BaseParam':
        json_string = json.dumps(obj).replace(" ", "")
        secret = self.get_secret(16)
        sha256_hash = hashlib.sha256(json_string.encode())
        encrypted_hash = sha256_hash.hexdigest()

        base_param = BaseParam()
        base_param.data = AesCoder.http_encrypt(json_string, secret)
        base_param.value = RSAEncrypt.rsa_encrypt(secret, public_key)
        base_param.hash = encrypted_hash
        return base_param

    async def login(self, username, password, publicKey):
        params = {
            "accountType": 1,
            "appVersion": "5.1.13",
            "intlPhoneCode": 86,
            "osType": 2,
            "osVersion": "12",
            "password": hashlib.sha256(password.encode('utf-8')).hexdigest(),
            "terminalId": self.appTerminalId,
            "terminalModel": self.appTerminalModel,
            "terminalName": self.appTerminalModel,
            "timestamp": int(time.time() * 1000),
            "uniqueCode": str(uuid.uuid4()),
            "username": username
        }

        params = self.encrypt_params(params, publicKey)
        baseRequest = BaseRequest()
        baseRequest.params = params.to_dict()
        baseRequest.seq = 93

        session = async_get_clientsession(self._hass)
        async with session.post(
                f"{self.BASE_URL}/rest/app/community/user/encryptV1Login",
                verify_ssl=False,
                json=baseRequest.to_dict(),
        ) as res:
            res.raise_for_status()
            data = await res.json(encoding="utf-8")
            return data



  

    async def get_devices(self, did, direct_did, seq=1):
        url = f"{self.BASE_URL}/rest/app/community/smartHome/getDevices"
        params = {
            "did": did,
            "directDid": direct_did
        }
        LogUtils.d("HttpApi", f"getDevices 请求: did={did}, directDid={direct_did}")
        result = await self._make_request(url, params, seq)
        LogUtils.d("HttpApi", f"getDevices 返回: result={result.get('result')}")
        return result

    async def query_logic_service_detail(self, did, direct_did, group_id, siid, seq=1):
        url = f"{self.BASE_URL}/rest/app/community/smartHome/queryLogicServiceDetail"
        params = {
            "did": did,
            "directDid": direct_did,
            "groupId": group_id,
            "siid": siid
        }
        return await self._make_request(url, params, seq)

    async def read_dids_fiids(self, did, direct_did, fiids, siid, is_real_date=0, seq=1):
        url = f"{self.BASE_URL}/rest/app/community/readDidsFIIDS"
        params = [{
            "did": did,
            "directDid": direct_did,
            "fiids": fiids,
            "isRealDate": is_real_date,
            "siid": siid
        }]
        return await self._make_request(url, params, seq)

    async def encrypt_v1_ctrl_fiids(self, siid, direct_did, fiids, did, seq=1):
        url = f"{self.BASE_URL}/rest/app/community/encryptV1CtrlFIIDS"
        params = {
            "siid": siid,
            "directDid": direct_did,
            "fiids": fiids,
            "did": did
        }
        LogUtils.d("HttpApi", f"encrypt_v1_ctrl_fiids 请求: siid={siid}, directDid={direct_did}, fiids={fiids}, did={did}")
        return await self._make_request(url, params, seq)

    async def batch_get_device_online_info(self, did, direct_did, seq=1):
        url = f"{self.BASE_URL}/rest/app/community/dc/batchGetDeviceOnlineInfo"
        params = [{
            "did": did,
            "directDid": direct_did
        }]
        LogUtils.d("HttpApi", f"batch_get_device_online_info 请求: did={did}, directDid={direct_did}")
        return await self._make_request(url, params, seq)

    async def get_device_permission(self, siid, group_id, direct_did, is_shared=0, did=None, seq=1):
        url = f"{self.BASE_URL}/rest/app/community/mergeHomes/getDevicePermission"
        params = {
            "siid": siid,
            "groupId": group_id,
            "directDid": direct_did,
            "isShared": is_shared
        }
        if did:
            params["did"] = did
        return await self._make_request(url, params, seq)

    async def batch_get_device_fiid_value(self, did, direct_did, fiid_list, profile_id, siid, seq=1):
        url = f"{self.BASE_URL}/rest/app/community/dp/batchGetDeviceFiidValue"
        params = [{
            "did": did,
            "directDid": direct_did,
            "fiidList": fiid_list,
            "profileId": profile_id,
            "siid": siid
        }]
        return await self._make_request(url, params, seq)