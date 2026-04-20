# Pool Pump Controller - TODO

## Goal
Create a working ESPHome controller for a **Jandy VS-FHP1.0 VSFloPro** variable-speed pool pump
using M5Stack ATOM Lite + ATOMIC RS485 Base, controlled via Home Assistant.

## Why CenturyVSPump Doesn't Work

CenturyVSPump sends **EPC Modbus RTU** packets:
```
[addr=0x15] [func] [ACK=0x20] [data...] [CRC16-lo] [CRC16-hi]
                                          ^^^^^^^^^^^^^^^^^^^ CRC-16 Modbus
```
With idle-time framing (3.5 char gaps, NO start/stop bytes).

Our Jandy pump speaks the **Jandy DLE variant**:
```
[10 02] [addr=0x78] [func] [data...] [sum_cs] [10 03]
 ^^^^^                                 ^^^^^^   ^^^^^
 DLE STX                            1-byte sum  DLE ETX
```

Every single layer is incompatible: framing, address, ACK byte, checksum, and demand encoding.

## Confirmed Protocol (from minicom.cap reverse-engineering)

### Packet Structure
```
10 02  [addr]  [func]  [data...]  [cs]  10 03
 ^preamble                         ^    ^postamble
                            1-byte checksum
```
- **Preamble / Postamble:** `10 02` (DLE STX) / `10 03` (DLE ETX)
- **Escape:** literal `0x10` in data is transmitted as `10 10`
- **Checksum:** `sum(0x10, 0x02, addr, func, data...) & 0xFF` — 1-byte simple sum, NOT CRC-16
- **Baud rate:** 9600 baud, 8N1, half-duplex RS-485

### Addresses
| Address | Role |
|---------|------|
| `0x78`  | Destination for commands sent TO pump |
| `0x1F`  | Pump source address for Status/Sensor/Demand responses |
| `0x20`  | Pump source address for ReadID and Config responses |
| `0x01`  | Pump source address for Go/Stop ACKs |

### Functions and Packet Formats
| Func | Name | Command (addr=`0x78`) | Response |
|------|------|-----------------------|----------|
| `0x41` | Go | `78 41 [cs]` | `01 41 [cs]` |
| `0x42` | Stop | `78 42 [cs]` | `01 42 [cs]` |
| `0x43` | Status | `78 43 [cs]` | `1F 43 [cs]` (stopped) or `1F 43 [status] [cs]` (running) |
| `0x44` | Set Demand | `78 44 [dem_lo] [dem_hi] [cs]` | `1F 44 [dem_lo] [dem_hi] [cs]` |
| `0x45` | Read Sensor | `78 45 [sensor_addr] [cs]` | `1F 45 [sensor_addr] [val_lo] [val_hi] [cs]` |
| `0x46` | Read ID | `78 46 [page] [cs]` | `20 46 [data...] [cs]` |
| `0x64` | Config R/W | `78 64 [page] [cs]` | `20 64 [data...] [cs]` |
| `0x65` | Store Config | `78 65 [cs]` | `01 65 [cs]` |

### RPM / Demand Encoding
- `demand_value = RPM × 4`, stored as little-endian 16-bit
- Example: 2750 RPM → 11000 = `0x2AF8` → bytes `F8 2A`
- Valid range: 600–3450 RPM in 50 RPM steps (demand 2400–13800)
- CenturyVSPump uses 3-byte demand `[mode, lo, hi]`; Jandy uses 2-byte `[lo, hi]`

### Motor Status Byte (in Status response)
| Value | Meaning |
|-------|---------|
| none / `0x00` | Motor stopped |
| `0x09` | Run mode – boot (motor getting ready) |
| `0x0B` | Run mode – vector (running normally) |
| `0x20` | Fault mode – motor stopped |

### Known Quirks
1. **Checksum +5 quirk:** Some packets from the original controller (addr `0x20`, func `0x64`)
   have a checksum that is consistently 5 higher than the standard formula.
2. **minicom strips `0x0B`:** When capturing with minicom, the byte `0x0B` (ASCII VT) is silently
   dropped. This affects ~1473 of 6274 packets in `minicom.cap`. `parse_cap.py` reconstructs these.

---

## Implementation Steps

### Step 1 — Create ESPHome external component skeleton [DONE]
Create `components/jandypump/` modeled after CenturyVSPump but using raw UART (not modbus):
- `__init__.py` — component registration, YAML config schema (uart-based)
- `const.py` — shared constants
- `jandy_pump.h` — C++ class declaration
- `jandy_pump.cpp` — core protocol: DLE framing, checksum, TX/RX, command queue
- `sensor/__init__.py` + C++ — RPM sensor platform
- `switch/__init__.py` + C++ — on/off switch platform
- `number/__init__.py` + C++ — target RPM number platform

### Step 2 — Implement DLE protocol engine (jandy_pump.cpp) [DONE]
Core UART protocol layer:
- `send_command()`: build inner bytes → compute checksum → DLE-frame → UART write
- `receive_loop()`: buffer UART bytes → detect `10 02...10 03` → unescape `10 10` → validate checksum → dispatch
- Flow control: drive GPIO23 HIGH during TX, LOW for RX (half-duplex RS-485)
- Command queue with 10ms throttle and 5x retry (same as CenturyVSPump)
- Periodic status polling (every ~2s to keep pump in remote mode)

### Step 3 — Implement sensor/switch/number platforms [DONE]
- **Switch (on/off):** Send Stop or Set Demand + Go commands
- **Number (target RPM):** Send Set Demand with RPM×4 little-endian encoding
- **Sensor (current RPM):** Read Sensor page 0 addr 0x00, scale value/4
- Optional sensors: motor status, watts (page 0 addr 0x0A), DC bus voltage

### Step 4 — Write poolpump.yaml for the new component [DONE]
Replace the modbus/centuryvspump YAML with jandypump config:
```yaml
external_components:
  - source: components

uart:
  id: pump_uart
  tx_pin: GPIO19
  rx_pin: GPIO22
  baud_rate: 9600

jandypump:
  uart_id: pump_uart
  flow_control_pin: GPIO23

switch:
  - platform: jandypump
    name: "Pool Pump"

sensor:
  - platform: jandypump
    name: "Pool Pump RPM"
    type: rpm

number:
  - platform: jandypump
    name: "Pool Pump Target RPM"
    min_value: 600
    max_value: 3450
    step: 50
```

### Step 5 — Compile and test [DONE - compiles clean]
- `esphome compile poolpump.yaml` — verify it builds
- Flash to device via USB
- Check ESPHome logs for UART TX/RX debug output
- Verify pump responds to Status query first (non-destructive)
- Test Set Demand + Go sequence

### Step 6 — Power solution [ ]
DIP switch #1 must be OFF for Jandy DLE protocol → no 12V from pump.
Options:
- USB power brick near the pump equipment
- Run USB cable from nearby outlet
- Or: test if DIP #1 ON still responds to DLE packets (some pumps accept both)

---

## Reference Files
- `minicom.cap` — 50,183 bytes, 6,274 packets of captured working RS-485 traffic
- `pg/parse_cap.py` — authoritative parser (fully validated against capture)
- `Gen3-EPC-Modbus-Communication-Protocol-_Rev4.17.pdf` — official Century EPC protocol spec
- `grok_chat.md` — initial protocol notes, wiring diagram, Arduino sample code
- CenturyVSPump source (GitHub: gazoodle/CenturyVSPump) — reference for ESPHome component structure
