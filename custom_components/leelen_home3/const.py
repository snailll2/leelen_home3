DOMAIN = "leelen3"
NAME = "Leelen Home3"
VERSION = "0.4.0"

CONF_PHONE = "phone"
CONF_DEVICE_ADDR = "deviceAddr"
CONF_ACCOUNT_ID = "accountId"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_ACCESS_TOKEN = "accessToken"
CONF_REFRESH_TOKEN = "refreshToken"
CONF_GATEWAY_IP = "gateway_ip"
CONF_GROUP_ID = "groupId"
CONF_MQTT_CLIENT_ID = "mqtt_client_id"
CONF_MQTT_USERNAME = "mqtt_username"

OPTIONS_CONFIG = "config"
OPTIONS_SELECT = "select"

SUPPORTED_PLATFORMS = [
    "sensor",
    "climate",
    "switch",
    "light",
    "fan",
    "cover",
]

ATTR_ON_OFF = "onOff"
ATTR_TEMPERATURE = "setTemp"
ATTR_MODE = "mode"
ATTR_WIND_SPEED = "windSpeed"

HVAC_MODE_COOL = "cool"
HVAC_MODE_HEAT = "heat"
HVAC_MODE_AUTO = "auto"
HVAC_MODE_FAN_ONLY = "fan_only"
HVAC_MODE_OFF = "off"

FAN_SPEED_LOW = 1
FAN_SPEED_MEDIUM = 2
FAN_SPEED_HIGH = 3

# Logical service types live in device_catalog.py, named after the official
# app's IotDeviceServiceType constants (decompiled from com.leelen.community).
# The old PROFILE_* placeholders here were wrong: 8260 is the 485 floor-heating
# server (not a switch) and 8262 is a bed service (not a light).
