"""Sensor platform for Shimpanssi."""

import asyncio
import logging
import struct
import time
from typing import Any

from bleak import BleakClient, BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import BluetoothCallbackMatcher
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
    StateType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_MAC,
    PERCENTAGE,
    UnitOfLength,
    UnitOfSpeed,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import format_mac

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CHAR_UUID = "00002ac1-5348-494d-414e-4f5f424c4500"
BATT_UUID = "00002a19-0000-1000-8000-00805f9b34fb"


# Runtime data belonging to a single config entry.
class ShimpanssiRuntimeData:
    """Runtime data for a Shimpanssi config entry."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.entities: list[ShimpanssiSensor] = []
        self.is_fetching: bool = False
        self.last_update_time: float = 0.0


type ShimpanssiConfigEntry = ConfigEntry[ShimpanssiRuntimeData]


# ============================================================================
# Entity descriptions (All sensors in a single list)
# ============================================================================

SENSOR_DESCRIPTIONS = (
    SensorEntityDescription(
        key="odometer",
        translation_key="odometer",
        native_unit_of_measurement=UnitOfLength.METERS,
        suggested_display_precision=0,
        suggested_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:bike-fast",
    ),
    SensorEntityDescription(
        key="battery",
        translation_key="battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="trip_distance",
        translation_key="trip_distance",
        native_unit_of_measurement=UnitOfLength.METERS,
        suggested_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:map-marker-distance",
    ),
    SensorEntityDescription(
        key="trip_time",
        translation_key="trip_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-outline",
    ),
    SensorEntityDescription(
        key="trip_speed_max",
        translation_key="trip_speed_max",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        suggested_display_precision=1,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
    ),
    SensorEntityDescription(
        key="trip_speed_average",
        translation_key="trip_speed_average",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        suggested_display_precision=1,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer-medium",
    ),
    SensorEntityDescription(
        key="range_eco",
        translation_key="range_eco",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:leaf",
    ),
    SensorEntityDescription(
        key="range_trail",
        translation_key="range_trail",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:bike",
    ),
    SensorEntityDescription(
        key="range_boost",
        translation_key="range_boost",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=0,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt",
    ),
)


# ============================================================================
# Setup
# ============================================================================


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ShimpanssiConfigEntry, async_add_entities
) -> None:
    """Set up the Shimpanssi sensor platform from a config entry."""

    mac = config_entry.data[CONF_MAC]

    # Runtime data belongs to this config entry instead of a module-global dict.
    runtime_data = ShimpanssiRuntimeData()
    runtime_data.data = {description.key: None for description in SENSOR_DESCRIPTIONS}
    config_entry.runtime_data = runtime_data

    # Create sensor entities dynamically.
    entities = [
        ShimpanssiSensor(runtime_data, mac, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    async_add_entities(entities)

    @callback
    def _async_ble_device_found(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle BLE advertisement from the bike."""

        _LOGGER.debug("BLE advertisement: %s", service_info.advertisement)

        # Defensive guards only.
        #
        # These checks are not required for normal operation. Home Assistant
        # and BlueZ already handle advertisement deduplication, so this
        # callback is usually triggered only once per discovery cycle.
        #
        # The guards below exist solely to reduce the risk of accidental
        # concurrent connection attempts if unusual Bluetooth events occur.

        current_time = time.monotonic()
        if runtime_data.is_fetching or (
            current_time - runtime_data.last_update_time < 300
        ):
            return

        runtime_data.is_fetching = True
        runtime_data.last_update_time = current_time

        # Trigger the background task to fetch data
        hass.async_create_task(
            async_fetch_bike_data(hass, mac, runtime_data, service_info.device)
        )

    config_entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_ble_device_found,
            BluetoothCallbackMatcher(address=mac),
            bluetooth.BluetoothChange.ADVERTISEMENT,
        )
    )


# ============================================================================
# BLE communication
# ============================================================================


async def async_fetch_bike_data(
    hass: HomeAssistant,
    mac: str,
    runtime_data: ShimpanssiRuntimeData,
    ble_device: BLEDevice,
) -> None:
    """Fetch data from the bike and notify sensors."""

    try:
        client = await establish_connection(
            BleakClient,
            ble_device,
            ble_device.name,
        )

        try:
            # 1. Read standard Bluetooth Battery Service.
            try:
                batt_bytes = await client.read_gatt_char(BATT_UUID)
                runtime_data.data["battery"] = batt_bytes[0]
                _LOGGER.debug("Battery read from standard service: %s%%", batt_bytes[0])
            except BleakError as err:
                _LOGGER.debug("Could not read standard battery service: %s", err)

            # 2. Listen for Shimano custom packets.
            data_received = asyncio.Event()
            received_messages = {"02": False, "03": False}

            def notification_handler(sender, data: bytearray) -> None:
                """Handle Shimano BLE notification packets."""
                _LOGGER.debug("Received raw packet: %s", data.hex(" "))

                if len(data) >= 10 and data[0] == 0x02:
                    runtime_data.data["trip_time"] = struct.unpack_from("<I", data, 6)[
                        0
                    ]
                    received_messages["02"] = True

                elif len(data) >= 19 and data[0] == 0x03:
                    runtime_data.data["trip_distance"] = struct.unpack_from(
                        "<I", data, 1
                    )[0]
                    runtime_data.data["odometer"] = struct.unpack_from("<I", data, 5)[0]
                    tmp = struct.unpack_from("<H", data, 9)[0]
                    runtime_data.data["trip_speed_average"] = (
                        round(tmp / 10.0, 1) if tmp else 0
                    )
                    tmp = struct.unpack_from("<H", data, 11)[0]
                    runtime_data.data["trip_speed_max"] = (
                        round(tmp / 10.0, 1) if tmp else 0
                    )
                    runtime_data.data["range_boost"] = struct.unpack_from(
                        "<H", data, 13
                    )[0]
                    runtime_data.data["range_trail"] = struct.unpack_from(
                        "<H", data, 15
                    )[0]
                    runtime_data.data["range_eco"] = struct.unpack_from("<H", data, 17)[
                        0
                    ]
                    received_messages["03"] = True

                if received_messages["02"] and received_messages["03"]:
                    data_received.set()

            await client.start_notify(CHAR_UUID, notification_handler)
            await asyncio.wait_for(data_received.wait(), timeout=10.0)

        finally:
            # Cleanly stop notifications before disconnecting
            if client.is_connected:
                try:
                    await client.stop_notify(CHAR_UUID)
                except BleakError as err:
                    _LOGGER.debug("Could not stop notifications: %s", err)

            try:
                await client.disconnect()
            except BleakError as err:
                _LOGGER.debug("Disconnect failed: %s", err)

    except TimeoutError:
        _LOGGER.debug("Timeout waiting for data packets from Shimano E-Bike.")
    except Exception:
        # Using .exception() so parsing errors reveal the full stack trace
        _LOGGER.exception(
            "Connection or data parsing error with Shimano E-Bike (%s)", mac
        )
    finally:
        # Release the connection lock
        runtime_data.is_fetching = False

        # Update all registered sensor entities
        for entity in runtime_data.entities:
            entity.async_write_ha_state()


# ============================================================================
# Entities (Single generic class)
# ============================================================================


class ShimpanssiSensor(SensorEntity):
    """Dynamic representation of a Shimpanssi sensor."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        runtime_data: ShimpanssiRuntimeData,
        mac: str,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        self._runtime_data = runtime_data
        self._mac = mac
        self.entity_description = description

        self._attr_unique_id = f"{format_mac(mac)}_{description.key}"

        # Link all sensors to the same physical device in the Device Registry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, format_mac(mac))},
            "name": "Shimano E-Bike",
            "manufacturer": "Shimano",
            "model": "EP800",
        }

    async def async_added_to_hass(self) -> None:
        """Register entity when added to Home Assistant."""
        self._runtime_data.entities.append(self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister entity when removed from Home Assistant."""
        if self in self._runtime_data.entities:
            self._runtime_data.entities.remove(self)

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor dynamically."""
        # Fetch the value directly using the description key
        return self._runtime_data.data.get(self.entity_description.key)
