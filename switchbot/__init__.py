"""Library to handle connection with Switchbot."""
from __future__ import annotations

import asyncio
import binascii
import logging
import time
from typing import Any
from uuid import UUID

import bleak

DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_TIMEOUT = 1
DEFAULT_SCAN_TIMEOUT = 5

# Switchbot device BTLE handles
UUID_SERVICE = UUID("{cba20d00-224d-11e6-9fb8-0002a5d5c51b}")
HANDLE = UUID("{cba20002-224d-11e6-9fb8-0002a5d5c51b}")
NOTIFICATION_HANDLE = UUID("{cba20003-224d-11e6-9fb8-0002a5d5c51b}")

# Keys common to all device types
DEVICE_GET_BASIC_SETTINGS_KEY = "5702"
DEVICE_SET_MODE_KEY = "5703"
DEVICE_SET_EXTENDED_KEY = "570f"

# Bot keys
PRESS_KEY = "570100"
ON_KEY = "570101"
OFF_KEY = "570102"

# Curtain keys
OPEN_KEY = "570f450105ff00"  # 570F4501010100
CLOSE_KEY = "570f450105ff64"  # 570F4501010164
POSITION_KEY = "570F450105ff"  # +actual_position ex: 570F450105ff32 for 50%
STOP_KEY = "570F450100ff"
CURTAIN_EXT_SUM_KEY = "570f460401"
CURTAIN_EXT_ADV_KEY = "570f460402"
CURTAIN_EXT_CHAIN_INFO_KEY = "570f468101"

# Keys used when encryption is set
KEY_PASSWORD_PREFIX = "571"

_LOGGER = logging.getLogger(__name__)
CONNECT_LOCK = asyncio.Lock()


def _process_wohand(data: bytes) -> dict[str, bool | int]:
    """Process woHand/Bot services data."""
    _bot_data: dict[str, bool | int] = {}

    # 128 switch or 0 press.
    _bot_data["switchMode"] = bool(data[1] & 0b10000000)

    # 64 off or 0 for on, if not inversed in app.
    if _bot_data["switchMode"]:
        _bot_data["isOn"] = not bool(data[1] & 0b01000000)

    else:
        _bot_data["isOn"] = False

    _bot_data["battery"] = data[2] & 0b01111111

    return _bot_data


def _process_wocurtain(data: bytes, reverse: bool = True) -> dict[str, bool | int]:
    """Process woCurtain/Curtain services data."""
    _curtain_data: dict[str, bool | int] = {}

    _curtain_data["calibration"] = bool(data[1] & 0b01000000)
    _curtain_data["battery"] = data[2] & 0b01111111
    _curtain_data["inMotion"] = bool(data[3] & 0b10000000)
    _position = max(min(data[3] & 0b01111111, 100), 0)
    _curtain_data["position"] = (100 - _position) if reverse else _position

    # light sensor level (1-10)
    _curtain_data["lightLevel"] = (data[4] >> 4) & 0b00001111
    _curtain_data["deviceChain"] = data[4] & 0b00000111

    return _curtain_data


def _process_wosensorth(data: bytes) -> dict[str, Any]:
    """Process woSensorTH/Temp sensor services data."""
    _wosensorth_data: dict[str, Any] = {}

    _temp_sign = 1 if data[4] & 0b10000000 else -1
    _temp_c = _temp_sign * ((data[4] & 0b01111111) + (data[3] / 10))
    _temp_f = (_temp_c * 9 / 5) + 32
    _temp_f = (_temp_f * 10) / 10

    _wosensorth_data["temp"] = {}
    _wosensorth_data["temp"]["c"] = _temp_c
    _wosensorth_data["temp"]["f"] = _temp_f

    _wosensorth_data["fahrenheit"] = bool(data[5] & 0b10000000)
    _wosensorth_data["humidity"] = data[5] & 0b01111111
    _wosensorth_data["battery"] = data[2] & 0b01111111

    return _wosensorth_data


class GetSwitchbotDevices:
    """Scan for all Switchbot devices and return by type."""

    def __init__(self, interface: int = 0) -> None:
        """Get switchbot devices class constructor."""
        self._interface = f"hci{interface}"
        self._all_services_data: dict[str, Any] = {}
        self._data = {}

    def detection_callback(self, device, advertisement_data):
        """BTLE adv scan callback."""
        _device = device.address.replace(":", "")
        _service_data = list(advertisement_data.service_data.values())[0]
        _model = chr(_service_data[0] & 0b01111111)

        self._data[_device] = {
            "mac_address": device.address,
            "service_data": list(advertisement_data.service_data.values())[0],
            "isEncrypted": bool(_service_data[0] & 0b10000000),
            "model": _model,
            "data": {
                "rssi": device.rssi,
            },
        }

        if _model == "H":
            self._data[_device]["modelName"] = "WoHand"
            self._data[_device]["data"] = _process_wohand(_service_data)
        elif _model == "c":
            self._data[_device]["modelName"] = "WoCurtain"
            self._data[_device]["data"] = _process_wocurtain(_service_data)
        elif _model == "T":
            self._data[_device]["modelName"] = "WoSensorTH"
            self._data[_device]["data"] = _process_wosensorth(_service_data)

    async def discover(
        self,
        retry: int = DEFAULT_RETRY_COUNT,
        scan_timeout: int = DEFAULT_SCAN_TIMEOUT,
        passive: bool = False,
    ) -> dict | None:
        """Find switchbot devices and their advertisement data."""

        devices = None

        devices = bleak.BleakScanner(
            filters={"UUIDs": [str(UUID_SERVICE)]}, adapter=self._interface
        )
        devices.register_detection_callback(self.detection_callback)
        await devices.start()
        await asyncio.sleep(scan_timeout)
        await devices.stop()

        if devices is None:
            if retry < 1:
                _LOGGER.error(
                    "Scanning for Switchbot devices failed. Stop trying", exc_info=True
                )
                return None

            _LOGGER.warning(
                "Error scanning for Switchbot devices. Retrying (remaining: %d)",
                retry,
            )
            time.sleep(DEFAULT_RETRY_TIMEOUT)
            return self.discover(retry - 1, scan_timeout, passive)

        return self._data

    def get_curtains(self) -> dict:
        """Return all WoCurtain/Curtains devices with services data."""
        if not self._all_services_data:
            self.discover()

        _curtain_devices = {}

        for device, data in self._all_services_data.items():
            if data.get("model") == "c":
                _curtain_devices[device] = data

        return _curtain_devices

    def get_bots(self) -> dict:
        """Return all WoHand/Bot devices with services data."""
        if not self._all_services_data:
            self.discover()

        _bot_devices = {}

        for device, data in self._all_services_data.items():
            if data.get("model") == "H":
                _bot_devices[device] = data

        return _bot_devices

    def get_tempsensors(self) -> dict:
        """Return all WoSensorTH/Temp sensor devices with services data."""
        if not self._all_services_data:
            self.discover()

        _bot_temp = {}

        for device, data in self._all_services_data.items():
            if data.get("model") == "T":
                _bot_temp[device] = data

        return _bot_temp

    def get_device_data(self, mac: str) -> dict:
        """Return data for specific device."""
        if not self._all_services_data:
            self.discover()

        _switchbot_data = {}

        for device in self._all_services_data.values():
            if device["mac_address"] == mac:
                _switchbot_data = device

        return _switchbot_data


class SwitchbotDevice:
    """Base Representation of a Switchbot Device."""

    def __init__(
        self,
        mac: str,
        password: str | None = None,
        interface: int = 0,
        **kwargs: Any,
    ) -> None:
        """Switchbot base class constructor."""
        self._interface = f"hci{interface}"
        self._mac = mac
        self._device = bleak.BleakClient(mac)
        self._switchbot_device_data: dict[str, Any] = {}
        self._scan_timeout: int = kwargs.pop("scan_timeout", DEFAULT_SCAN_TIMEOUT)
        self._retry_count: int = kwargs.pop("retry_count", DEFAULT_RETRY_COUNT)
        if password is None or password == "":
            self._password_encoded = None
        else:
            self._password_encoded = "%x" % (
                binascii.crc32(password.encode("ascii")) & 0xFFFFFFFF
            )

    async def _connect(self) -> None:
        try:
            _LOGGER.debug("Connecting to Switchbot")
            await self._device.connect()
            _LOGGER.debug("Connected to Switchbot")
        except bleak.BleakError:
            _LOGGER.debug("Failed connecting to Switchbot", exc_info=True)
            raise

    async def _disconnect(self) -> None:
        _LOGGER.debug("Disconnecting")
        try:
            await self._device.disconnect()
        except bleak.BleakError:
            _LOGGER.warning("Error disconnecting from Switchbot", exc_info=True)

    async def _commandkey(self, key: str) -> str:
        if self._password_encoded is None:
            return key
        key_action = key[3]
        key_suffix = key[4:]
        return KEY_PASSWORD_PREFIX + key_action + self._password_encoded + key_suffix

    async def _writekey(self, key: str) -> Any:
        _LOGGER.debug("Sending command, %s", key)
        await self._device.write_gatt_char(HANDLE, bytearray.fromhex(key), False)

    async def keypress_handler(sender, data, other):
        """Test method for notification responses."""
        print(sender, data, other)

    async def _subscribe(self) -> None:
        _LOGGER.debug("Subscribe to notifications")
        await self._device.start_notify(NOTIFICATION_HANDLE, self.keypress_handler)

    async def _readkey(self) -> bytes:
        _LOGGER.debug("Prepare to read")
        receive_handle = await self._device.read_gatt_char(NOTIFICATION_HANDLE)
        print("Receive message", receive_handle)
        return receive_handle

    async def _sendcommand(self, key: str, retry: int) -> bytes:
        command = self._commandkey(key)
        notify_msg = b"\x00"
        _LOGGER.debug("Sending command to switchbot %s", command)

        try:
            await self._connect()
            await self._subscribe()
            await self._writekey(command)
            notify_msg = await self._readkey()
        except bleak.BleakError:
            _LOGGER.warning("Error talking to Switchbot", exc_info=True)
        finally:
            await self._disconnect()
        if notify_msg:
            if notify_msg == b"\x07":
                _LOGGER.error("Password required")
            elif notify_msg == b"\t":
                _LOGGER.error("Password incorrect")

            return notify_msg
        if retry < 1:
            _LOGGER.error(
                "Switchbot communication failed. Stopping trying", exc_info=True
            )
            return notify_msg
        _LOGGER.warning("Cannot connect to Switchbot. Retrying (remaining: %d)", retry)
        time.sleep(DEFAULT_RETRY_TIMEOUT)
        return self._sendcommand(key, retry - 1)

    async def get_mac(self) -> str:
        """Return mac address of device."""
        return self._mac

    async def get_battery_percent(self) -> Any:
        """Return device battery level in percent."""
        if not self._switchbot_device_data:
            return None
        return self._switchbot_device_data["data"]["battery"]

    async def get_device_data(
        self,
        retry: int = DEFAULT_RETRY_COUNT,
        interface: int | None = None,
        passive: bool = False,
    ) -> dict | None:
        """Find switchbot devices and their advertisement data."""
        if interface:
            _interface: int | None = interface
        else:
            _interface = self._interface

        self._switchbot_device_data = await GetSwitchbotDevices(
            interface=_interface
        ).get_device_data(mac=self._mac)

        if self._switchbot_device_data is None:
            if retry < 1:
                _LOGGER.error(
                    "Scanning for Switchbot devices failed. Stop trying", exc_info=True
                )
                return None

            _LOGGER.warning(
                "Error scanning for Switchbot devices. Retrying (remaining: %d)",
                retry,
            )
            time.sleep(DEFAULT_RETRY_TIMEOUT)
            return self.get_device_data(
                retry=retry - 1, interface=_interface, passive=passive
            )

        return self._switchbot_device_data


class Switchbot(SwitchbotDevice):
    """Representation of a Switchbot."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Switchbot Bot/WoHand constructor."""
        super().__init__(*args, **kwargs)
        self._inverse: bool = kwargs.pop("inverse_mode", False)
        self._settings: dict[str, Any] = {}

    async def update(self, interface: int | None = None, passive: bool = False) -> None:
        """Update mode, battery percent and state of device."""
        await self.get_device_data(
            retry=self._retry_count, interface=interface, passive=passive
        )

    async def turn_on(self) -> bool:
        """Turn device on."""
        result = await self._sendcommand(ON_KEY, self._retry_count)

        if result[0] == 1:
            return True

        if result[0] == 5:
            _LOGGER.warning("Bot is in press mode and doesn't have on state")
            return True

        return False

    async def turn_off(self) -> bool:
        """Turn device off."""
        result = await self._sendcommand(OFF_KEY, self._retry_count)
        if result[0] == 1:
            return True

        if result[0] == 5:
            _LOGGER.warning("Bot is in press mode and doesn't have off state")
            return True

        return False

    async def press(self) -> bool:
        """Press command to device."""
        result = await self._sendcommand(PRESS_KEY, self._retry_count)
        if result[0] == 1:
            return True

        if result[0] == 5:
            _LOGGER.warning("Bot is in switch mode")
            return True

        return False

    async def set_switch_mode(
        self, switch_mode: bool = False, strength: int = 100, inverse: bool = False
    ) -> bool:
        """Change bot mode."""
        mode_key = format(switch_mode, "b") + format(inverse, "b")
        strength_key = f"{strength:0{2}x}"  # to hex with padding to double digit

        result = await self._sendcommand(
            DEVICE_SET_MODE_KEY + strength_key + mode_key, self._retry_count
        )

        if result[0] == 1:
            return True

        return False

    async def set_long_press(self, duration: int = 0) -> bool:
        """Set bot long press duration."""
        duration_key = f"{duration:0{2}x}"  # to hex with padding to double digit

        result = await self._sendcommand(
            DEVICE_SET_EXTENDED_KEY + "08" + duration_key, self._retry_count
        )

        if result[0] == 1:
            return True

        return False

    async def get_basic_info(self) -> dict[str, Any] | None:
        """Get device basic settings."""
        settings_data = await self._sendcommand(
            key=DEVICE_GET_BASIC_SETTINGS_KEY, retry=self._retry_count
        )

        if not settings_data:
            _LOGGER.warning("Unsuccessfull, please try again")
            return None

        if settings_data in (b"\x07", b"\x00"):
            return None

        self._settings["battery"] = settings_data[1]
        self._settings["firmware"] = settings_data[2] / 10.0
        self._settings["strength"] = settings_data[3]

        self._settings["timers"] = settings_data[8]
        self._settings["switchMode"] = bool(settings_data[9] & 16)
        self._settings["inverseDirection"] = bool(settings_data[9] & 1)
        self._settings["holdSeconds"] = settings_data[10]

        return self._settings

    async def switch_mode(self) -> Any:
        """Return true or false from cache."""
        # To get actual position call update() first.
        if not self._switchbot_device_data:
            return None
        return self._switchbot_device_data["data"]["switchMode"]

    async def is_on(self) -> Any:
        """Return switch state from cache."""
        # To get actual position call update() first.
        if not self._switchbot_device_data:
            return None

        if self._inverse:
            return not self._switchbot_device_data["data"]["isOn"]

        return self._switchbot_device_data["data"]["isOn"]


class SwitchbotCurtain(SwitchbotDevice):
    """Representation of a Switchbot Curtain."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Switchbot Curtain/WoCurtain constructor."""

        # The position of the curtain is saved returned with 0 = open and 100 = closed.
        # This is independent of the calibration of the curtain bot (Open left to right/
        # Open right to left/Open from the middle).
        # The parameter 'reverse_mode' reverse these values,
        # if 'reverse_mode' = True, position = 0 equals close
        # and position = 100 equals open. The parameter is default set to True so that
        # the definition of position is the same as in Home Assistant.

        super().__init__(*args, **kwargs)
        self._reverse: bool = kwargs.pop("reverse_mode", True)
        self._settings: dict[str, Any] = {}
        self.ext_info_sum: dict[str, Any] = {}
        self.ext_info_adv: dict[str, Any] = {}

    async def open(self) -> bool:
        """Send open command."""
        result = await self._sendcommand(OPEN_KEY, self._retry_count)
        if result[0] == 1:
            return True

        return False

    async def close(self) -> bool:
        """Send close command."""
        result = await self._sendcommand(CLOSE_KEY, self._retry_count)
        if result[0] == 1:
            return True

        return False

    async def stop(self) -> bool:
        """Send stop command to device."""
        result = await self._sendcommand(STOP_KEY, self._retry_count)
        if result[0] == 1:
            return True

        return False

    async def set_position(self, position: int) -> bool:
        """Send position command (0-100) to device."""
        position = (100 - position) if self._reverse else position
        hex_position = "%0.2X" % position
        result = await self._sendcommand(POSITION_KEY + hex_position, self._retry_count)
        if result[0] == 1:
            return True

        return False

    async def update(self, interface: int | None = None, passive: bool = False) -> None:
        """Update position, battery percent and light level of device."""
        await self.get_device_data(
            retry=self._retry_count, interface=interface, passive=passive
        )

    async def get_position(self) -> Any:
        """Return cached position (0-100) of Curtain."""
        # To get actual position call update() first.
        if not self._switchbot_device_data:
            return None
        return self._switchbot_device_data["data"]["position"]

    async def get_basic_info(self) -> dict[str, Any] | None:
        """Get device basic settings."""
        settings_data = await self._sendcommand(
            key=DEVICE_GET_BASIC_SETTINGS_KEY, retry=self._retry_count
        )

        if not settings_data:
            _LOGGER.warning("Unsuccessfull, please try again")
            return None

        if settings_data in (b"\x07", b"\x00"):
            return None

        self._settings["battery"] = settings_data[1]
        self._settings["firmware"] = settings_data[2] / 10.0

        self._settings["chainLength"] = settings_data[3]

        self._settings["openDirection"] = (
            "right_to_left" if settings_data[4] & 0b10000000 == 128 else "left_to_right"
        )

        self._settings["touchToOpen"] = bool(settings_data[4] & 0b01000000)
        self._settings["light"] = bool(settings_data[4] & 0b00100000)
        self._settings["fault"] = bool(settings_data[4] & 0b00001000)

        self._settings["solarPanel"] = bool(settings_data[5] & 0b00001000)
        self._settings["calibrated"] = bool(settings_data[5] & 0b00000100)
        self._settings["inMotion"] = bool(settings_data[5] & 0b01000011)

        _position = max(min(settings_data[6], 100), 0)
        self._settings["position"] = (100 - _position) if self._reverse else _position

        self._settings["timers"] = settings_data[7]

        return self._settings

    async def get_extended_info_summary(self) -> dict[str, Any] | None:
        """Get basic info for all devices in chain."""
        data = await self._sendcommand(key=CURTAIN_EXT_SUM_KEY, retry=self._retry_count)
        if not data or data[0] != 1:
            _LOGGER.warning("Unsuccessfull, please try again")
            return None

        self.ext_info_sum["device0"] = {}
        self.ext_info_sum["device0"]["openDirectionDefault"] = not bool(
            data[1] & 0b10000000
        )
        self.ext_info_sum["device0"]["touchToOpen"] = bool(data[1] & 0b01000000)
        self.ext_info_sum["device0"]["light"] = bool(data[1] & 0b00100000)
        self.ext_info_sum["device0"]["openDirection"] = (
            "left_to_right" if data[1] & 0b00010000 == 1 else "right_to_left"
        )

        if data[2] != 0:
            self.ext_info_sum["device1"] = {}
            self.ext_info_sum["device1"]["openDirectionDefault"] = not bool(
                data[1] & 0b10000000
            )
            self.ext_info_sum["device1"]["touchToOpen"] = bool(data[1] & 0b01000000)
            self.ext_info_sum["device1"]["light"] = bool(data[1] & 0b00100000)
            self.ext_info_sum["device1"]["openDirection"] = (
                "left_to_right" if data[1] & 0b00010000 else "right_to_left"
            )

        return self.ext_info_sum

    async def get_extended_info_adv(self) -> dict[str, Any] | None:
        """Get advance page info for device chain."""

        data = await self._sendcommand(key=CURTAIN_EXT_ADV_KEY, retry=self._retry_count)

        if not data or data[0] != 1:
            _LOGGER.warning("Unsuccessfull, please try again")
            return None

        self.ext_info_adv["device0"] = {}
        self.ext_info_adv["device0"]["battery"] = data[1]
        self.ext_info_adv["device0"]["firmware"] = data[2] / 10.0

        if data[3] == 0:
            self.ext_info_adv["device0"]["stateOfCharge"] = "not_charging"
        elif data[3] == 1:
            self.ext_info_adv["device0"]["stateOfCharge"] = "charging_by_adapter"
        elif data[3] == 2:
            self.ext_info_adv["device0"]["stateOfCharge"] = "charging_by_solar"
        elif data[3] == 3 or data[3] == 4:
            self.ext_info_adv["device0"]["stateOfCharge"] = "fully_charged"
        elif data[3] == 5:
            self.ext_info_adv["device0"]["stateOfCharge"] = "solar_not_charging"
        elif data[3] == 6:
            self.ext_info_adv["device0"]["stateOfCharge"] = "charging_error"

        if data[4]:
            self.ext_info_adv["device1"] = {}
            self.ext_info_adv["device1"]["battery"] = data[4]
            self.ext_info_adv["device1"]["firmware"] = data[5] / 10.0

            if data[6] == 0:
                self.ext_info_adv["device0"]["stateOfCharge"] = "not_charging"
            elif data[6] == 1:
                self.ext_info_adv["device0"]["stateOfCharge"] = "charging_by_adapter"
            elif data[6] == 2:
                self.ext_info_adv["device0"]["stateOfCharge"] = "charging_by_solar"
            elif data[6] == 3 or data[6] == 4:
                self.ext_info_adv["device0"]["stateOfCharge"] = "fully_charged"
            elif data[6] == 5:
                self.ext_info_adv["device0"]["stateOfCharge"] = "solar_not_charging"
            elif data[6] == 6:
                self.ext_info_adv["device0"]["stateOfCharge"] = "charging_error"

        return self.ext_info_adv

    async def get_light_level(self) -> Any:
        """Return cached light level."""
        # To get actual light level call update() first.
        if not self._switchbot_device_data:
            return None
        return self._switchbot_device_data["data"]["lightLevel"]

    async def is_reversed(self) -> bool:
        """Return True if curtain position is opposite from SB data."""
        return self._reverse

    async def is_calibrated(self) -> Any:
        """Return True curtain is calibrated."""
        # To get actual light level call update() first.
        if not self._switchbot_device_data:
            return None
        return self._switchbot_device_data["data"]["calibration"]
