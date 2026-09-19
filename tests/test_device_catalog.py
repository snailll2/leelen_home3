"""Regression tests for Leelen logical-device discovery."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "leelen_home3"
    / "device_catalog.py"
)


def load_catalog_module():
    spec = importlib.util.spec_from_file_location("leelen_device_catalog", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def physical(did, name, device_type, profile_id):
    return {
        "did": did,
        "directDid": "gateway",
        "name": name,
        "deviceType": device_type,
        "profileId": str(profile_id),
        "roomName": "",
        "softModel": "test-model",
    }


def details(did, services):
    return {
        "result": 1,
        "params": [
            {
                "physicDevice": {"did": did},
                "logicDevices": [
                    {
                        "did": did,
                        "directDid": "gateway",
                        "siid": siid,
                        "logicName": name,
                        "purposeTypeName": purpose,
                        "profileId": profile_id,
                        "serviceType": service_type,
                    }
                    for siid, name, purpose, profile_id, service_type in services
                ],
            }
        ],
    }


LIVE_ACCOUNT_FIXTURE = [
    (
        physical("ac-module", "空调模块", 8215, 834),
        details(
            "ac-module",
            [
                (2, "次卧1中央空调", "中央空调", 834, 8259),
                (3, "次卧2中央空调", "中央空调", 834, 8259),
                (4, "书房中央空调", "中央空调", 834, 8259),
                (5, "主卧中央空调", "中央空调", 834, 8259),
                (6, "客厅中央空调", "中央空调", 834, 8259),
                (7, "餐厅中央空调", "中央空调", 834, 8259),
            ],
        ),
    ),
    (
        physical("heating-module", "ZigBee 地暖执行器", 8218, 463),
        details(
            "heating-module",
            [
                (2, "次卧1地暖", "地暖", 463, 8268),
                (3, "次卧2地暖", "地暖", 463, 8268),
                (4, "书房地暖", "地暖", 463, 8268),
                (5, "主卧地暖", "地暖", 463, 8268),
                (6, "客餐厅地暖", "地暖", 463, 8268),
            ],
        ),
    ),
    (
        physical("fresh-air-module", "新风模块", 8215, 834),
        details(
            "fresh-air-module",
            [
                (2, "新风", "新风", 834, 8261),
                (3, "新风执行服务", "新风", 834, 8267),
            ],
        ),
    ),
    *[
        (
            physical(f"panel-{index}", f"{room}温控面板", 8214, profile_id),
            details(
                f"panel-{index}",
                [
                    (2, "空调控制器", "空调控制器", profile_id, 8254),
                    (3, "新风控制器", "新风控制器", profile_id, 8255),
                    (4, "地暖控制器", "地暖控制器", profile_id, 8256),
                    (5, f"{room}传感器", "传感器", profile_id, 8272),
                ],
            ),
        )
        for index, (room, profile_id) in enumerate(
            [
                ("次卧1", 201),
                ("次卧2", 201),
                ("次卧3", 622),
                ("主卧", 201),
                ("客厅", 622),
                ("餐厅", 622),
            ],
            start=1,
        )
    ],
    (
        physical("temp-sensor-1", "温湿度传感器", 8214, 341),
        details(
            "temp-sensor-1",
            [(2, "主卧温湿度", "温湿度传感器", 341, 8246)],
        ),
    ),
    (
        physical("lamp-module", "ZigBee 灯具模块", 12802, 342),
        details(
            "lamp-module",
            [
                (2, "客厅灯", "灯", 342, 8212),
                (3, "客厅调光灯", "调光灯", 342, 8291),
            ],
        ),
    ),
    (
        physical("socket-module", "智能插座", 12802, 342),
        details(
            "socket-module",
            [(2, "厨房插座", "智能插座", 342, 8243)],
        ),
    ),
    (
        physical("curtain-module", "窗帘电机", 12802, 343),
        details(
            "curtain-module",
            [
                (2, "客厅窗帘", "窗帘", 343, 8232),
                (3, "主卧百叶帘", "百叶帘", 343, 8317),
            ],
        ),
    ),
    (
        physical("breaker-1", "照明空开", 8205, 37),
        details(
            "breaker-1",
            [(2, "照明空开", "空开断路器", 37, 8200)],
        ),
    ),
    (
        physical("gateway", "zigbee无线网关", 8196, 588),
        details("gateway", [(2, "无线网关", "网关服务", 588, 8211)]),
    ),
]


class DeviceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog_module()
        self.devices = [
            self.catalog.normalize_device(physical_device, detail_response)
            for physical_device, detail_response in LIVE_ACCOUNT_FIXTURE
        ]

    def test_routes_live_logical_services_to_home_assistant_platforms(self):
        climate = list(self.catalog.iter_platform_services(self.devices, "climate"))
        fan = list(self.catalog.iter_platform_services(self.devices, "fan"))
        sensor = list(self.catalog.iter_platform_services(self.devices, "sensor"))
        light = list(self.catalog.iter_platform_services(self.devices, "light"))
        switch = list(self.catalog.iter_platform_services(self.devices, "switch"))
        cover = list(self.catalog.iter_platform_services(self.devices, "cover"))

        self.assertEqual(11, len(climate))
        self.assertEqual(2, len(fan))
        self.assertEqual(7, len(sensor))
        self.assertEqual(2, len(light))
        self.assertEqual(2, len(switch))
        self.assertEqual(2, len(cover))

        self.assertEqual(
            [
                "次卧1中央空调",
                "次卧2中央空调",
                "书房中央空调",
                "主卧中央空调",
                "客厅中央空调",
                "餐厅中央空调",
            ],
            [service["logic_name"] for _, service in climate[:6]],
        )

    def test_logical_service_metadata_wins_over_physical_device_type(self):
        air_conditioner = self.devices[0]
        first_service = air_conditioner["logic_srv"][0]

        self.assertEqual(8215, air_conditioner["device_type"])
        self.assertEqual(8259, first_service["service_type"])
        self.assertEqual(834, first_service["profile_id"])
        self.assertEqual("次卧1中央空调", first_service["logic_name"])
        self.assertEqual("ac-module_2", first_service["service_id"])

    def test_ignores_panel_controller_and_gateway_services(self):
        routed = {
            service["service_type"]
            for platform in (
                "climate",
                "fan",
                "sensor",
                "light",
                "switch",
                "cover",
            )
            for _, service in self.catalog.iter_platform_services(
                self.devices, platform
            )
        }

        self.assertEqual(
            {
                8259,
                8261,
                8267,
                8268,
                8272,
                8246,
                8212,
                8291,
                8243,
                8232,
                8317,
                8200,
            },
            routed,
        )

    def test_extracts_temperature_from_sensor_payloads(self):
        self.assertEqual(23.5, self.catalog.extract_temperature(23.5))
        self.assertEqual(24.0, self.catalog.extract_temperature("24"))
        self.assertEqual(25.0, self.catalog.extract_temperature({"curTemp": 25}))
        self.assertEqual(
            26.5,
            self.catalog.extract_temperature({"temperature": "26.5", "humidity": 40}),
        )
        self.assertIsNone(self.catalog.extract_temperature({"humidity": 40}))

    def test_builds_the_same_unique_id_used_by_platform_entities(self):
        device = self.devices[0]
        service = device["logic_srv"][0]

        self.assertEqual(
            "leelen_climate_ac-module_2",
            self.catalog.entity_unique_id(device, service, "climate"),
        )


if __name__ == "__main__":
    unittest.main()
