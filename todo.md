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

## Confirmed Protocol (from minicom.cap + AqualinkD cross-reference)

### Packet Structure
```
10 02  [addr]  [func]  [data...]  [cs]  10 03
 ^preamble                         ^    ^postamble
                            1-byte checksum
```
- **Preamble / Postamble:** `10 02` (DLE STX) / `10 03` (DLE ETX)
- **Escape:** literal `0x10` in data is transmitted as `10 00` (DLE NUL), NOT `10 10`
- **Checksum:** `sum(0x10, 0x02, addr, func, data...) & 0xFF` — 1-byte simple sum, NOT CRC-16
- **Baud rate:** 9600 baud, 8N1, half-duplex RS-485

### Addresses
| Address | Role |
|---------|------|
| `0x78`  | Destination for commands sent TO pump |
| `0x00`  | Destination address in responses (master/controller) |
| `0x1F`  | Pump source address for Status/Sensor/Demand responses |
| `0x20`  | Pump source address for ReadID and Config responses |
| `0x01`  | Pump source address for Go/Stop ACKs |

### Functions and Packet Formats (CORRECTED — see RESEARCH.md)

Commands 0x44, 0x45, and 0x46 all require a leading `0x00` "page" byte that minicom stripped:

| Func | Name | Command (addr=`0x78`) | Response |
|------|------|-----------------------|----------|
| `0x41` | Go | `78 41 [cs]` | `00 01 41 00 [cs]` |
| `0x42` | Stop | `78 42 [cs]` | `00 01 42 00 [cs]` |
| `0x43` | Status | `78 43 [cs]` | `00 1F 43 [status] [pad...] [cs]` |
| `0x44` | Set Demand | `78 44 00 [dem_lo] [dem_hi] [cs]` | `00 1F 44 00 [dem_lo] [dem_hi] 00 [cs]` |
| `0x45` | Read Sensor | `78 45 00 [sensor_addr] [cs]` | `00 1F 45 00 [sensor_addr] [val_lo] [val_hi] [cs]` |
| `0x46` | Read ID | `78 46 00 00 [page] [cs]` | `00 20 46 [data...] [cs]` |
| `0x64` | Config R/W | `78 64 [page] [cs]` | `00 20 64 [data...] [cs]` |
| `0x65` | Store Config | `78 65 [cs]` | `00 01 65 00 [cs]` |

### RPM / Demand Encoding
- `demand_value = RPM * 4`, stored as little-endian 16-bit
- Example: 2750 RPM -> 11000 = `0x2AF8` -> bytes `F8 2A`
- Valid range: 600-3450 RPM in 50 RPM steps (demand 2400-13800)

### Motor Status Byte (in Status response)
| Value | Meaning |
|-------|---------|
| none / `0x00` | Motor stopped |
| `0x09` | Run mode - boot (motor getting ready) |
| `0x0B` | Run mode - vector (running normally) |
| `0x20` | Fault mode - motor stopped |

### Known Quirks
1. **Checksum +5 quirk:** Some packets from the original controller (addr `0x20`, func `0x64`)
   have a checksum that is consistently 5 higher than the standard formula.
2. **minicom strips `0x0B`:** When capturing with minicom, the byte `0x0B` (ASCII VT) is silently
   dropped. This affects ~1473 of 6274 packets in `minicom.cap`. `parse_cap.py` reconstructs these.
3. **minicom strips `0x00`:** minicom also strips NUL bytes. This hid the `0x00` page bytes in
   commands AND the `0x00` destination address in responses. See RESEARCH.md for details.
4. **DLE escape is `10 00` not `10 10`:** AqualinkD documents the escape as inserting NUL after DLE.

---

## Implementation Steps

### Step 1 - Create ESPHome external component skeleton [DONE]
### Step 2 - Implement DLE protocol engine (jandy_pump.cpp) [DONE]
### Step 3 - Implement sensor/switch/number platforms [DONE]
### Step 4 - Write poolpump.yaml for the new component [DONE]
### Step 5 - Compile and test [DONE - compiles clean]

### Step 6 - Fix missing 0x00 page bytes (CURRENT)
Apply findings from RESEARCH.md:
- [ ] Add `0x00` page byte to Set Demand command: `78 44 00 [lo] [hi]`
- [ ] Add `0x00` page byte to Read Sensor command: `78 45 00 [sensor_addr]`
- [ ] Add `0x00 0x00` prefix to ReadID command: `78 46 00 00 [page]`
- [ ] Fix DLE escape from `10 10` to `10 00` (TX and RX)
- [ ] Update response parsing for extra `0x00` bytes in responses
- [ ] Update "bare Read Sensor" - was actually `78 45 00` (page byte only)
- [ ] Test: sensors should update in HA
- [ ] Test: speed change should work
- [ ] Test: ReadID/Config should get responses

### Step 7 - Power solution [ ]
DIP switch #1 must be OFF for Jandy DLE protocol -> no 12V from pump.
Options:
- USB power brick near the pump equipment
- Run USB cable from nearby outlet
- Or: test if DIP #1 ON still responds to DLE packets (some pumps accept both)

---

## Reference Files
- `minicom.cap` - 50,183 bytes, 6,274 packets of captured working RS-485 traffic
- `pg/parse_cap.py` - authoritative parser (fully validated against capture)
- `Gen3-EPC-Modbus-Communication-Protocol-_Rev4.17.pdf` - official Century EPC protocol spec
- `grok_chat.md` - initial protocol notes, wiring diagram, Arduino sample code
- `RESEARCH.md` - AqualinkD cross-reference findings (missing 0x00 bytes, DLE escape)
- CenturyVSPump source (GitHub: gazoodle/CenturyVSPump) - reference for ESPHome component structure
- AqualinkD source (GitHub: aqualinkd/AqualinkD) - ePump protocol reference (authoritative)
