# ESPHome Jandy/Zodiac VS-FHP Pool Pump Controller

Version 0.5.0 - Working, Alpha

Custom [ESPHome](https://esphome.io/) component to control **Jandy/Zodiac VS-FHP (VSFloPro)** variable-speed pool pumps via RS-485, replacing the original Jandy controller. Exposes pump on/off, target RPM, sensor data, and quick-set speed buttons to [Home Assistant](https://www.home-assistant.io/).

## Disclaimer/Warning
I built this with Claude with RS-485 logging and various resources like: https://github.com/gazoodle/CenturyVSPump and https://github.com/aqualinkd/AqualinkD

As an amature **I may have overlooked an importaint safety or other issue.**  I am using the this code and belive it to work safely, but use at your own risk.

Feel free to leave feedback.


## Installation

### As an ESPHome External Component

Add to your ESPHome YAML the contents of `poolpump.yaml`

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

## Supported Entities

| Entity | Type | Description |
|--------|------|-------------|
| Pool Pump Run | Switch | Start/stop the pump |
| Pool Pump Target RPM | Number | Set target speed (600-3450 RPM, 50 RPM steps) |
| Pool Pump Current RPM | Sensor | Read current pump speed |
| Pool Pump Power | Sensor | Motor output power (W) |
| Pool Pump Input Power | Sensor | Inverter input power (W) |
| Pool Pump Motor Current | Sensor | Motor current draw (A) |
| Pool Pump DC Bus Voltage | Sensor | DC bus voltage (V) |
| Pool Pump 600/2000/2600/3450 RPM | Buttons | Quick-set speed presets |

## Safety Shutoff

The `poolpump.yaml` config includes a safety feature that automatically stops the pump if communication with Home Assistant is lost. This protects against the pump running indefinitely if HA crashes, the network goes down, or the WiFi drops.

**How it works:**
1. When the HA API connection drops, a configurable timer starts (default: 120 seconds).
2. If HA reconnects within the timeout, the timer is cancelled and the pump keeps running.
3. If HA is still disconnected after the timeout, the pump is stopped.

**Configuration:** Edit the `safety_shutoff_timeout` substitution in `poolpump.yaml`:

```yaml
substitutions:
  # Seconds to wait after HA disconnects before stopping the pump.
  # Set to "0" to disable (pump keeps running indefinitely without HA).
  safety_shutoff_timeout: "120"
```

## Protocol

This component implements the **Jandy DLE-framed variant** of the Century EPC protocol. This is **NOT** standard Modbus RTU - every layer differs:

| Feature | Jandy DLE (this component) | EPC Modbus RTU (CenturyVSPump) |
|---------|---------------------------|-------------------------------|
| Framing | `10 02` ... `10 03` (DLE STX/ETX) | Idle-time gaps |
| Checksum | 1-byte simple sum | CRC-16 Modbus |
| Pump address | `0x78` | `0x15` |
| Demand encoding | 3 bytes `[00, lo, hi]`, RPM x 4 | 3 bytes `[mode, lo, hi]` |
| DLE escaping | `10 00` (DLE NUL) | N/A (no DLE framing) |
| ACK byte | None | `0x20` in every command |

The protocol was reverse-engineered from captured RS-485 traffic between a working Jandy controller and pump, and cross-referenced with the [AqualinkD](https://github.com/aqualinkd/AqualinkD) project. See `PROTOCOL.md` for full protocol documentation.

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

## References

- [AqualinkD](https://github.com/aqualinkd/AqualinkD) - Authoritative ePump protocol reference
- [CenturyVSPump](https://github.com/gazoodle/CenturyVSPump) - Modbus RTU variant (incompatible)
- [Gen3 EPC Modbus Communication Protocol Rev 4.17 (PDF)](https://www.troublefreepool.com/) - Official Century EPC spec (Jandy variant differs)

## License

MIT License - see [LICENSE](LICENSE).
