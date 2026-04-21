# Jandy DLE Protocol Reference

Reverse-engineered protocol documentation for **Jandy/Zodiac VS-FHP (VSFloPro)** variable-speed pool pumps using the Century EPC motor. This protocol is used on the RS-485 link between the pump controller (UI board) and the pump drive board.

This document was produced by:
1. Capturing RS-485 traffic between a working Jandy controller and pump (`minicom.cap`, 50,183 bytes, 6,274 packets)
2. Parsing and analyzing the capture with `pg/parse_cap.py`
3. Live testing with an M5Stack ATOM Lite + ATOMIC RS485 Base running the `jandypump` ESPHome component

## Protocol Overview

This is the **Jandy DLE-framed variant** of the Century EPC (Electronically Commutated Pump) protocol. It is **NOT** standard Modbus RTU. The official reference is the "Gen3 EPC Modbus Communication Protocol Rev 4.17" PDF, but the Jandy variant differs in framing, checksum, addresses, and demand encoding.

### Physical Layer

| Parameter | Value |
|-----------|-------|
| Interface | RS-485 half-duplex |
| Baud rate | 9600 |
| Data bits | 8 |
| Parity | None |
| Stop bits | 1 |
| Direction control | DE/RE pin: HIGH = transmit, LOW = receive |

### Wiring (4-wire pump connector)

| Wire Color | Signal | Description |
|------------|--------|-------------|
| RED | +12V | Power supply from pump (when DIP #1 = ON) |
| GREEN | GND | Ground reference |
| BLACK | DT+ (A) | RS-485 data positive |
| YELLOW | DT- (B) | RS-485 data negative |

---

## Packet Structure

### DLE Framing

All packets are wrapped in DLE (Data Link Escape) framing:

```
10 02  [addr] [func] [data...]  [checksum]  10 03
^^^^                                        ^^^^
DLE STX (preamble)                     DLE ETX (postamble)
```

- **Preamble:** `0x10 0x02` (DLE STX)
- **Postamble:** `0x10 0x03` (DLE ETX)
- **DLE escape:** A literal `0x10` byte inside the data is transmitted as `0x10 0x10`. The receiver must unescape these.
- **DLE in state machine terms:**
  - `10 02` = start of frame
  - `10 03` = end of frame
  - `10 10` = escaped literal `0x10`
  - Any other `10 XX` in the data stream is a protocol error

### Checksum

**1-byte simple sum** (NOT CRC-16):

```
checksum = (0x10 + 0x02 + addr + func + data_bytes...) & 0xFF
```

The checksum is computed over the preamble bytes (`0x10`, `0x02`) plus all inner bytes (address, function, data) **before** DLE escaping. The checksum byte itself is also subject to DLE escaping when transmitted.

#### Checksum +5 Quirk

Config (`0x64`) commands and all responses from address `0x20` (ReadID and Config responses) use a checksum that is **5 higher** than the standard formula:

```
checksum_quirk = (standard_checksum + 5) & 0xFF
```

This is a firmware quirk present in both the original Jandy controller board and the pump drive board. When **sending** Config commands, the checksum must include the +5 offset or the pump will reject them. When **receiving** responses from addr `0x20`, both standard and +5 checksums should be accepted.

This quirk was confirmed by:
1. Every Config command in `minicom.cap` (105 packets) uses checksum+5
2. Every ReadID response from addr `0x20` (7 packets) uses checksum+5
3. Live testing: the pump ignored Config commands sent with standard checksum, breaking the init handshake

### Example Packet (Go command)

```
Command:  10 02  78  41  CB  10 03
          ^^^^   ^^  ^^  ^^  ^^^^
          DLE   addr func cs  DLE
          STX   0x78 Go       ETX

Checksum: (0x10 + 0x02 + 0x78 + 0x41) & 0xFF = 0xCB
```

---

## Addresses

### Command Destination

| Address | Role |
|---------|------|
| `0x78` | Pump drive board (default for single-pump installations) |

Pump address is set by DIP switches on the pump PCB. Range `0x78`-`0x81` for pumps 1-4.

### Response Source Addresses

The pump uses **different source addresses** depending on the command being responded to:

| Address | Used For |
|---------|----------|
| `0x01` | Go (`0x41`) and Stop (`0x42`) acknowledgements |
| `0x1F` | Status (`0x43`), Set Demand (`0x44`), Read Sensor (`0x45`) responses |
| `0x20` | Read Identification (`0x46`), Config R/W (`0x64`) responses |
| `0xFF` | NACK (negative acknowledgement / error) responses |

---

## Function Codes

### 0x41 — Go (Start Motor)

Starts the motor at the previously set demand speed.

| Direction | Packet |
|-----------|--------|
| Command | `10 02  78 41  [cs]  10 03` |
| Response (ACK) | `10 02  01 41  [cs]  10 03` |

No payload. The motor will enter boot mode (`0x09`) and then transition to vector/running mode (`0x0B`).

### 0x42 — Stop

Stops the motor.

| Direction | Packet |
|-----------|--------|
| Command | `10 02  78 42  [cs]  10 03` |
| Response (ACK) | `10 02  01 42  [cs]  10 03` |

No payload.

### 0x43 — Status

Queries the current motor status.

| Direction | Packet |
|-----------|--------|
| Command | `10 02  78 43  [cs]  10 03` |
| Response (stopped) | `10 02  1F 43  [cs]  10 03` |
| Response (running) | `10 02  1F 43  [status] [pad...]  [cs]  10 03` |

The status byte values:

| Value | Meaning |
|-------|---------|
| *(absent)* | Motor stopped (response has no data bytes) |
| `0x00` | Motor stopped |
| `0x09` | Run mode: boot (motor initializing, getting ready to spin) |
| `0x0B` | Run mode: vector (running normally at target speed) |
| `0x20` | Fault mode (motor stopped due to error) |
| `0x23` | Run mode: extended status |

**Live test observation:** The status response includes trailing padding bytes (`00 00 00`) after the status byte. For example, a running pump returns `1F 43 0B 00 00 00` inside the DLE frame.

### 0x44 — Set Demand (Target RPM)

Sets the target speed for the motor.

| Direction | Packet |
|-----------|--------|
| Command | `10 02  78 44  [dem_lo] [dem_hi]  [cs]  10 03` |
| Response (ACK) | `10 02  1F 44  [dem_lo] [dem_hi]  [cs]  10 03` |

**Demand encoding:**
- `demand_value = RPM * 4`
- Stored as **little-endian unsigned 16-bit**
- Valid RPM range: 600-3450, in 50 RPM steps
- Valid demand range: 2400-13800

**Examples:**

| RPM | Demand | Bytes (little-endian) |
|-----|--------|-----------------------|
| 600 | 2400 | `60 09` |
| 2600 | 10400 | `A0 28` |
| 2750 | 11000 | `F8 2A` |
| 3450 | 13800 | `E8 35` |

**Important:** This is a 2-byte payload `[lo, hi]`. The Century EPC Modbus variant uses a 3-byte payload `[mode, lo, hi]` with a mode byte — the Jandy DLE variant omits the mode byte entirely.

### 0x45 — Read Sensor

Reads a sensor register from the pump.

| Direction | Packet |
|-----------|--------|
| Command | `10 02  78 45  [sensor_addr]  [cs]  10 03` |
| Response (ACK) | `10 02  1F 45  [sensor_addr] [val_lo] [val_hi]  [cs]  10 03` |

**Sensor address map (Page 0):**

| Addr | Name | Scale | Unit |
|------|------|-------|------|
| `0x00` | Motor Speed | /4 | RPM |
| `0x01` | Motor Current | /1000 | Amps |
| `0x02` | Operating Mode | — | 0=speed, 1=torque |
| `0x03` | Demand | /4 | RPM |
| `0x04` | Torque | /1200 | lb-ft |
| `0x05` | Inverter Input Power | /1 | Watts |
| `0x06` | DC Bus Voltage | /64 | Volts |
| `0x07` | Ambient Temperature | /128 | degrees C |
| `0x08` | Status | — | see status table |
| `0x09` | Previous Fault | — | fault code |
| `0x0A` | Output Power | /1 | Watts |
| `0x0B` | SVRS Bypass Status | — | — |
| `0x0C` | Num Current Faults | /1 | count |
| `0x0E` | Ramp Status | — | — |
| `0x0F` | Num Total Faults | /1 | count |
| `0x10` | Prime Status | — | 0=stopped, 1=running, 2=over |
| `0x12` | IGBT Temperature | /128 | degrees C |
| `0x14` | External Input Status | — | — |
| `0x15` | Reference Speed | /4 | RPM |

**Sensor address map (Page 1):**

| Addr | Name | Scale | Unit |
|------|------|-------|------|
| `0x07` | Serial Timeout Counter | /1 | count |
| `0x08` | Total Run Time Low | /1 | hours |
| `0x09` | Total Run Time High | /1 | hours |
| `0x0A` | Total Life Time Low | /1 | hours |
| `0x0B` | Total Life Time High | /1 | hours |
| `0x15`-`0x18` | Active Faults 1-4 | — | fault codes |

### 0x46 — Read Identification

Reads pump identification data (model, firmware version, etc.).

| Direction | Packet |
|-----------|--------|
| Command | `10 02  78 46  [page]  [cs]  10 03` |
| Response | `10 02  20 46  [data...]  [cs]  10 03` |

### 0x64 — Config Read/Write

Reads or writes pump configuration registers.

| Direction | Packet |
|-----------|--------|
| Command | `10 02  78 64  [page]  [cs]  10 03` |
| Response | `10 02  20 64  [data...]  [cs]  10 03` |

### 0x65 — Store Configuration

Commits configuration changes to non-volatile memory.

| Direction | Packet |
|-----------|--------|
| Command | `10 02  78 65  [cs]  10 03` |
| Response (ACK) | `10 02  01 65  [cs]  10 03` |

---

## Initialization Sequence

The pump **requires a ReadID/Config handshake** before it will accept Read Sensor (`0x45`) or Set Demand (`0x44`) commands. Without this handshake, these commands receive NACK code `0x03` (data out of range). Go (`0x41`), Stop (`0x42`), and Status (`0x43`) work without initialization.

### Observed Startup Sequence (from minicom.cap)

The original Jandy controller performs this sequence on power-up, cycling through sensor addresses and identification pages:

```
 1. Status (0x43)
 2. Read Sensor (0x45) — bare, no sensor address
 3. ReadID (0x46) page 3
 4. Config (0x64) page 6        ← uses checksum+5
 5. Status (0x43)
 6. Read Sensor (0x45) addr 0x01
 7. ReadID (0x46) page 4
 8. Config (0x64) page 6        ← uses checksum+5
 9. Status (0x43)
10. Read Sensor (0x45) addr 0x02
11. Config (0x64) page 6        ← uses checksum+5
12. Status (0x43)
13. Read Sensor (0x45) addr 0x03  → first successful sensor response (Demand = 600 RPM)
14. ReadID (0x46) page 3
15. Config (0x64) page 6        ← uses checksum+5
16. Status (0x43)
17. Read Sensor (0x45) addr 0x04
18. ReadID (0x46) page 4
```

After this sequence completes, the controller begins normal polling (Status + Read Sensor cycles) and can send Set Demand and Go commands.

### Key Details

- The **bare Read Sensor** (step 2) has no sensor address byte — just `78 45 [cs]`. The pump responds with an ACK but no value.
- **Config commands must use checksum+5** or the pump silently ignores them, breaking the handshake.
- **ReadID/Config responses come from addr `0x20`** with checksum+5. Receivers must accept this variant.
- The minimum required handshake steps are not fully determined — the `jandypump` component replays the full observed sequence to be safe.

---

## NACK (Error) Responses

When the pump rejects a command, it responds with source address `0xFF`:

```
10 02  FF  [func]  [nack_code]  [cs]  10 03
```

| NACK Code | Meaning |
|-----------|---------|
| `0x01` | Command not recognized / illegal |
| `0x02` | Operand out of allowed range |
| `0x03` | Data out of range |
| `0x04` | General failure: fault mode |
| `0x05` | Incorrect command length |
| `0x06` | Command cannot be executed now |

---

## Differences from Century EPC Modbus RTU

The official "Gen3 EPC Modbus Communication Protocol Rev 4.17" describes a Modbus RTU variant. The Jandy DLE protocol differs at every layer:

| Feature | Jandy DLE (this pump) | Century EPC Modbus RTU |
|---------|----------------------|----------------------|
| Framing | DLE STX/ETX (`10 02`/`10 03`) | Modbus RTU idle-time gaps (3.5 char silence) |
| Checksum | 1-byte simple sum | CRC-16 Modbus (polynomial 0xA001) |
| Pump address | `0x78` | `0x15` |
| ACK byte | None | `0x20` in every command |
| Demand payload | 2 bytes `[lo, hi]` (RPM x 4) | 3 bytes `[mode, lo, hi]` |
| DLE escaping | `0x10` -> `0x10 0x10` | N/A (no DLE framing) |
| Byte stuffing | Required for `0x10` in data | None |

This is why the [CenturyVSPump](https://github.com/gazoodle/CenturyVSPump) ESPHome component does not work with Jandy pumps.

---

## Live Test Results

### Test Setup (2026-04-20)
- **Controller:** M5Stack ATOM Lite + ATOMIC RS485 Base
- **Firmware:** ESPHome 2026.4.0 with custom `jandypump` component
- **Pump:** Jandy/Zodiac VS-FHP1.0 VSFloPro
- **Connection:** USB-C power to ATOM, RS-485 to pump (RED->12V, GREEN->GND, BLACK->A, YELLOW->B)

### Observations

#### Communication is working
The pump responds to every command. TX/RX timing is correct at 9600 baud with flow control on GPIO23.

**Commands sent and responses received:**

| TX (Command) | RX (Response) | Analysis |
|---|---|---|
| `10 02 78 43 CD 10 03` (Status) | `00 1F 43 00 00 00 00 74` | Status: stopped, with padding. Checksum valid. |
| `10 02 78 43 CD 10 03` (Status) | `00 1F 43 0B 00 00 00 7F` | Status: running (0x0B = vector mode), with padding. |
| `10 02 78 41 CB 10 03` (Go) | `00 01 41 00 54` | Go ACK from addr 0x01. Extra `00` byte after func. |
| `10 02 78 42 CC 10 03` (Stop) | `00 01 42 00 55` | Stop ACK from addr 0x01. Extra `00` byte after func. |
| `10 02 78 44 60 09 37 10 03` (Demand 600) | `00 FF 44 03 58` | **NACK!** addr=0xFF, code=0x03 (data out of range). |
| `10 02 78 45 00 CF 10 03` (Sensor 0x00) | `00 FF 45 03 59` | **NACK!** addr=0xFF, code=0x03 (data out of range). |
| `10 02 78 45 03 D2 10 03` (Sensor 0x03) | `00 FF 45 03 59` | **NACK!** addr=0xFF, code=0x03 (data out of range). |

#### Finding 1: Leading null byte in responses

Every response from the pump has a leading `0x00` byte that was **not present** in the original controller traffic captured in `minicom.cap`. This shifts all byte positions by one, breaking response parsing.

```
Expected (from minicom.cap):  1F 43 00 ...
Actual (live test):        00 1F 43 00 ...
                           ^^
                           unexpected null preamble
```

This may be caused by:
- The pump echoing back a partial byte when the controller releases the RS-485 bus (DE/RE transition artifact)
- A timing difference between the original controller and our flow-control pin switching

**Fix applied:** The parser now strips leading `0x00` bytes after checksum validation.

#### Finding 2: Go command works, pump runs

The Go command (`0x41`) successfully starts the pump:
1. Go command sent at 15:34:06.773
2. ACK received: `00 01 41 00 54`
3. Status changes from `0x00` (stopped) to `0x0B` (running) at 15:34:07.145
4. Pump runs for approximately 40 seconds
5. Pump stops on its own at ~15:34:47 (reverts to status `0x00`)

The pump stopped because no demand value was successfully set (all Set Demand commands received NACKs), and the command response matching was broken by the leading null byte, so the Go ACK was never processed by the firmware.

#### Finding 3: Set Demand and Read Sensor receive NACKs (RESOLVED)

All Set Demand (`0x44`) and Read Sensor (`0x45`) commands receive NACK code `0x03` (data out of range):

```
TX: 10 02 78 44 60 09 37 10 03    (Set Demand 600 RPM: demand=2400, bytes 60 09)
RX: 00 FF 44 03 58                 (NACK: data out of range)
```

**Root cause:** The pump requires a ReadID (`0x46`) and Config (`0x64`) initialization handshake before it will accept Read Sensor or Set Demand commands. See [Initialization Sequence](#initialization-sequence) above.

**Contributing factor:** Config commands must be sent with checksum+5, and Config/ReadID responses from the pump also use checksum+5. The firmware was dropping these responses as checksum mismatches, causing the init handshake to silently fail even after the init sequence code was added.

#### Finding 4: Status response has trailing padding

The status response from a running pump includes 3 extra `0x00` bytes after the status byte:

```
minicom.cap format:  1F 43 0B [cs]
Live test format:    1F 43 0B 00 00 00 [cs]
```

The additional padding bytes do not affect parsing as long as `data[2]` is used for the status value.

### Resolution Summary

| Issue | Status | Fix |
|-------|--------|-----|
| Leading null byte in responses | **Fixed** | Strip leading `0x00` after checksum validation |
| Set Demand / Read Sensor NACKs | **Fixed** | Added init handshake sequence (ReadID + Config) |
| Init handshake silently failing | **Fixed** | Send Config with checksum+5; accept +5 on RX |
| Sensor values not updating in HA | **Pending test** | Requires successful init handshake (fix above) |
| Speed change not working | **Pending test** | Requires successful init handshake (fix above) |

---

## Capture Analysis Tools

### parse_cap.py (primary parser)

Fully validated against `minicom.cap`. Handles DLE framing, checksum validation, minicom control character reconstruction, and all function code decoding.

```bash
python3 pg/parse_cap.py minicom.cap                    # full decode
python3 pg/parse_cap.py minicom.cap --limit 50         # first 50 packets
python3 pg/parse_cap.py minicom.cap --stats-only       # summary statistics
python3 pg/parse_cap.py minicom.cap --func 0x44        # filter by function
python3 pg/parse_cap.py minicom.cap --errors-only      # show problem packets
```

### Known Capture Artifacts

**minicom strips `0x0B`:** The `minicom.cap` file was recorded using minicom in terminal mode, which silently strips control characters. The byte `0x0B` (ASCII VT / vertical tab) is the most commonly affected, impacting approximately 1,473 of 6,274 packets. `parse_cap.py` detects and reconstructs these missing bytes automatically (labeled `CS:ART` in output).

**Checksum +5 quirk:** Config (`0x64`) commands and all addr `0x20` responses use checksum+5. This affects both sending and receiving — see [Checksum +5 Quirk](#checksum-5-quirk) for details. Failing to account for this breaks the initialization handshake.

---

## References

- [Gen3 EPC Modbus Communication Protocol Rev 4.17 (PDF)](https://www.troublefreepool.com/) — Official Century/Regal-Beloit protocol spec (Jandy DLE variant differs)
- [CenturyVSPump ESPHome Component](https://github.com/gazoodle/CenturyVSPump) — Modbus RTU variant (incompatible with Jandy DLE)
- [Trouble Free Pool: Jandy Pump Protocol](https://www.troublefreepool.com/threads/jandy-pump-protocol.265447/) — Community reverse-engineering discussion
- [ESPHome Jandy Pump Component](https://github.com/kasmok/esphome-jandy-pump) — This project
