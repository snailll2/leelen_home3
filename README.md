# Leelen Home3

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1.0-blue.svg)](https://www.home-assistant.io/)
[![Version](https://img.shields.io/badge/version-0.4.0-green.svg)]()

**English** | [简体中文](README.zh-CN.md)

Home Assistant integration for Leelen (立林) 3.0 smart home devices, driven by
the same cloud service (`iot.leelen.com`) used by the official Leelen app.

Integration domain: `leelen3` · iot_class: `cloud_push`

## Features

### Climate — central air conditioner (service type 8259)

- HVAC modes: off / heat / cool / fan only / dry
- Target temperature 5–35 °C with 1 °C step, fan speed low / medium / high
- Current temperature and humidity, automatically matched from the thermostat
  panel sensor of the same room
- Raw wind-speed gear exposed as the `leelen_wind_speed` state attribute

### Climate — floor heating (service type 8268)

- Modes: heat / off, target temperature 5–35 °C
- Current temperature from the matched thermostat panel sensor

### Fan — fresh-air system (service types 8261 / 8267)

- On/off with three speed gears, mapped to 33 % / 66 % / 100 % percentage steps
- Preset modes: low / medium / high

### Light (service types 8212 / 8291 / 8292 / 8293 / 8305 / 8306 / 8314 / 8330 / 8456 / 8459)

- On/off for every light type
- Brightness (0–100 % level) for dimmable lamps (dimmer, 0-10V, magnetic,
  slide and dual-color lamps)
- Color temperature (2700–6500 K) for color-temp capable lamps
- RGB color for RGB dimmer and magnetic track lamps

### Switch (service types 8243 / 8270 / 8295 / 8309 / 8333 / 8491 / 8200 / 8201 / 8202)

- On/off for smart sockets, single-channel dynamic panels, solenoid valves,
  valve servers, audible alarms and water purifiers
- Circuit breakers (空开断路器) are exposed as switches too; their switch
  value uses the base64 byte-stream format (`//8=` on, `AAA=` off)

### Cover — curtains (service types 8232 / 8294 / 8317 / 8320)

- Open / close / stop and position (0–100 %) via the curtain action and
  position FIIDs
- Louver curtains additionally support tilt position (百叶帘角度)

### Sensor — thermostat panels (service types 8272 / 8246 / 8289)

- One temperature (°C) and one humidity (%) entity per discovered panel or
  temperature/humidity sensor
- Values feed the climate entities of the same room as well

## How state sync works

The integration keeps device state fresh with three cooperating layers:

1. **REST snapshot** — on startup and whenever the topology changes, all FIID
   values are read from the cloud in one batched request.
2. **MQTT push (automatic, recommended)** — credentials are derived from your
   login (official app scheme), and the integration subscribes to the same
   `lliot/fiids_report/{did}/{siid}` topics the official app uses, so device
   reports arrive in near real time.
3. **Control confirmation** — `encryptV1CtrlFIIDS` only queues a command at the
   gateway (`waitNum`/`waitTime` in the response). After sending a command the
   integration therefore waits for a device report that matches the commanded
   values (up to the cloud's `waitTime` hint, capped at 10 s) and rejects
   outdated reports for up to 60 s while a command is pending. This prevents
   the UI from falling back to the pre-command state.

REST polling acts as the safety net: every 30 s while MQTT is unavailable, and
every 5 minutes as a background refresh while MQTT is connected.

## Account and token handling

- Login uses your Leelen phone number plus an SMS verification code
  (no account password required).
- Tokens are refreshed exactly the way the official app does: **lazily** —
  only when the cloud rejects a request with an expired-token code, followed
  by an automatic retry of that request. Concurrent refreshes are
  deduplicated with a 20-second cooldown so the rotating refresh token is
  never replayed (the classic cause of spurious logouts). New tokens are
  persisted in the config entry, so restarts do not require re-login.
- The device identity (terminal ID) is persisted across restarts like the
  official app instead of being regenerated every start.
- If the `refreshToken` itself expires, the integration raises a re-auth flow:
  confirm the phone number, enter a new SMS code, done.

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant.
2. Go to "Integrations" → click the "+" button.
3. Search for "Leelen Home" or add it as a custom repository:
   - Repository: `https://github.com/snailll2/leelen_home3`
   - Category: Integration
4. Click "Download". HACS installs the integration as
   `custom_components/leelen3` automatically (the manifest domain).
5. Restart Home Assistant.

### Manual Installation

1. Copy the folder `custom_components/leelen_home3` from this repository to
   your Home Assistant `config/custom_components` directory **and rename it to
   `leelen3`** — Home Assistant resolves custom integrations by folder name,
   and this integration's domain is `leelen3`.
2. Restart Home Assistant.

## Configuration

1. Go to **Settings** → **Devices & Services**.
2. Click **Add Integration** and search for "Leelen Home3" / "立林3.0".
3. Enter your Leelen app phone number; an SMS verification code is sent.
4. Enter the code. The config entry is titled with your home group name, e.g.
   `家庭组：我的家(138xxxxxxxx)`.

### Options

Open the integration options for two menus:

- **MQTT** — credentials are resolved **automatically**, the same way the
  official Leelen app does: the username is derived from your account
  (`a5e4x84a:<accountId>`), the password is the current access token, and the
  client ID is delivered by the cloud's token-refresh response. No manual
  input is required. Fill in both fields only to override the automatic
  values manually; leave both empty to keep automatic mode.
- **Refresh devices** — re-reads the device topology from the cloud, adds
  entities for new devices, removes entities for devices deleted in the Leelen
  app, reports the counts, and reloads the integration. Use this after adding
  or removing devices in the Leelen app.

## Supported devices

| Home Assistant platform | Leelen logical service | Service type |
|---|---|---:|
| climate | Central air conditioner, incl. current temperature and humidity | 8259 |
| climate | Floor heating, incl. current temperature | 8268 |
| fan | Fresh-air system | 8261, 8267 |
| light | Lights: on/off, dimmable, color temperature, RGB | 8212, 8291–8293, 8305, 8306, 8314, 8330, 8456, 8459 |
| switch | Smart socket, dynamic panel, valve, alarm, water purifier | 8243, 8270, 8295, 8309, 8333, 8491 |
| switch | Circuit breakers (base64 switch value) | 8200, 8201, 8202 |
| cover | Curtain motors incl. louver tilt | 8232, 8294, 8317, 8320 |
| sensor | Thermostat panel and temperature/humidity sensors | 8272, 8246, 8289 |

Service types follow the official app's `IotDeviceServiceType` constants.
Other Leelen services (smart terminal, background music, elevator control,
emergency button, locks, etc.) are ignored for now.

## Debug logging

To capture debug logs (including all cloud requests), add:

```yaml
logger:
  logs:
    custom_components.leelen3: debug
```

## Troubleshooting

### Cannot connect / login fails

- Ensure your Home Assistant can reach the Leelen cloud (`iot.leelen.com`).
- Check that your account is bound to a gateway device in the Leelen app.
- If HA reports the refresh token expired, use the re-auth dialog; older
  installations without a stored `refreshToken` must delete and re-add the
  integration.

### Devices not showing up

- Run **Refresh devices** from the integration options.
- Check the devices are online in the Leelen app.
- Only the logical service types listed above create entities.

### State updates look stale

- MQTT is configured automatically; check the log for the derived-credentials
  message, or force a token refresh by restarting HA.
- Without MQTT, state refreshes every 30 s via REST polling; commands confirm
  as soon as the device reports the new state.

## Support

If you encounter any issues, please
[open an issue](https://github.com/snailll2/leelen_home3/issues) on GitHub.

## License

This project is licensed under the MIT License.
