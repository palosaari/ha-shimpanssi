# Shimpanssi - Shimano E-bike Integration

A Home Assistant custom integration to connect and fetch data from Shimano e-bike motors via Bluetooth.
*Developed and tested on Shimano EP800, but should work with other Bluetooth-enabled Shimano motors.*

## Features

This integration provides the following sensors from your e-bike:
* **Battery Level** (%)
* **Odometer** (Total distance)
* **Estimated Ranges** (Remaining range based on assist modes)
* **Trip Distance**
* **Trip Time**
* **Trip Average Speed**
* **Trip Maximum Speed**

## Prerequisites

* Home Assistant instance with Bluetooth enabled (either via a built-in Bluetooth adapter or an ESPHome Bluetooth Proxy).
* It is highly recommended to have the e-bike turned on during setup so you can verify the connection immediately, though manual setup with a MAC address is also supported.

## Configuration

1. Go to **Settings** -> **Devices & Services** in Home Assistant.
2. Click **+ Add Integration** in the bottom right corner.
3. Search for **Shimpanssi** and follow the UI prompts to connect to your bike.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
