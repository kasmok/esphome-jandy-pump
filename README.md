# ESPHome Jandy/Zodiac VS-FHP Pool Pump Controller

Note: This code is pre-alpha and not tested.  Do not use.  I used claude with RS-485 logging and https://github.com/gazoodle/CenturyVSPump 

Feel free to leave feedback.

Custom [ESPHome](https://esphome.io/) component to control **Jandy/Zodiac VS-FHP (VSFloPro)** variable-speed pool pumps via RS-485, replacing the original Jandy controller. Exposes pump on/off, target RPM, and current RPM to [Home Assistant](https://www.home-assistant.io/).

## Hardware

- **ESP32 board:** [M5Stack ATOM Lite](https://docs.m5stack.com/en/core/ATOM%20Lite) (or any ESP32)
- **RS-485 interface:** [M5Stack ATOMIC RS485 Base](https://docs.m5stack.com/en/atom/Atomic%20RS485%20Base) (or any RS-485 transceiver with DE/RE flow control)
- **Pump:** Jandy/Zodiac VS-FHP1.0 VSFloPro (Century EPC motor with Jandy DLE protocol)

### Wiring (ATOMIC RS485 Base screw terminals to pump connector)

| RS485 Base | Pump Wire | Signal |
|------------|-----------|--------|
| 12V        | RED       | 12V power |
| GND        | GREEN     | Ground |
| A (DT+)    | BLACK     | RS-485 A |
| B (DT-)    | YELLOW    | RS-485 B |

> **DIP switch #1** on the ATOMIC RS485 Base: ON = Modbus mode (enables 12V power from pump). Test whether your pump accepts DLE packets with this ON. If not, power the ATOM Lite via USB instead.

## Installation

### As an ESPHome External Component

Add to your ESPHome YAML:

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/kasmok/esphome-jandy-pump
      ref: main

uart:
  id: pump_uart
  tx_pin: GPIO19   # Adjust for your board
  rx_pin: GPIO22
  baud_rate: 9600

jandypump:
  uart_id: pump_uart
  flow_control_pin: GPIO23  # DE/RE pin for half-duplex RS-485
  update_interval: 2s

switch:
  - platform: jandypump
    name: "Pool Pump Run"

sensor:
  - platform: jandypump
    name: "Pool Pump Current RPM"
    type: rpm
    unit_of_measurement: RPM

number:
  - platform: jandypump
    name: "Pool Pump Target RPM"
    unit_of_measurement: RPM
```

### Local Development

1. Clone this repo
2. Copy `secrets.yaml.example` to `secrets.yaml` and fill in your values
3. `esphome compile poolpump.yaml`
4. `esphome upload poolpump.yaml`

## Supported Entities

| Entity | Type | Description |
|--------|------|-------------|
| Pool Pump Run | Switch | Start/stop the pump |
| Pool Pump Target RPM | Number | Set target speed (600-3450 RPM, 50 RPM steps) |
| Pool Pump Current RPM | Sensor | Read current pump speed |

Optional sensor types: `watts` (motor power), `custom` (any sensor address).

## Protocol

This component implements the **Jandy DLE-framed variant** of the Century EPC protocol. This is **NOT** standard Modbus RTU - every layer differs:

| Feature | Jandy DLE (this component) | EPC Modbus RTU (CenturyVSPump) |
|---------|---------------------------|-------------------------------|
| Framing | `10 02` ... `10 03` (DLE STX/ETX) | Idle-time gaps |
| Checksum | 1-byte simple sum | CRC-16 Modbus |
| Pump address | `0x78` | `0x15` |
| Demand encoding | 2 bytes `[lo, hi]`, RPM x 4 | 3 bytes `[mode, lo, hi]` |
| ACK byte | None | `0x20` in every command |

The protocol was reverse-engineered from captured RS-485 traffic between a working Jandy controller and pump. See `CLAUDE.md` for full protocol documentation.

## Why Not CenturyVSPump?

The [CenturyVSPump](https://github.com/gazoodle/CenturyVSPump) ESPHome component sends EPC Modbus RTU packets. Jandy/Zodiac VS-FHP pumps speak a completely different DLE-framed protocol variant. The two are incompatible at every layer (framing, checksum, addresses, demand encoding). This component was built from scratch to speak the correct protocol.

## Compatible Pumps

Confirmed working:
- Jandy/Zodiac VS-FHP1.0 VSFloPro

Likely compatible (same Century EPC motor with Jandy DLE protocol):
- Jandy/Zodiac ePump
- Jandy/Zodiac VS-FHP2.0
- Other Jandy variable-speed pumps using DLE framing

If you test with a different pump model, please open an issue to report compatibility.

## License

MIT License - see [LICENSE](LICENSE).
