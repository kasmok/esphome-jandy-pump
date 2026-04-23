# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ESP32-based controller to replace a Zodiac ePump (Jandy) variable-speed pool pump controller. Communicates over RS-485 using the **Jandy proprietary protocol** (a variant of Century EPC, NOT standard Modbus RTU), exposing pump control to Home Assistant via WiFi.

## Build & Flash (ESPHome)

```bash
# Compile firmware
esphome compile poolpump.yaml

# Flash to device (USB required first time)
esphome flash poolpump.yaml

# Monitor logs
esphome logs poolpump.yaml
```

The primary config is `poolpump.yaml` (M5Stack ATOM Lite + ATOMIC RS485 Base). Uses a custom `jandypump` ESPHome component in `components/jandypump/` that implements the Jandy DLE protocol directly over UART (NOT through the ESPHome modbus component). `ESPHOME_PoolPump_Groked.yaml` is the old CenturyVSPump-based config (doesn't work — wrong protocol).

## Protocol Parser Scripts

The `pg/` directory contains Python scripts for analyzing RS-485 captures:

```bash
# Primary parser — fully validated against minicom.cap
python3 pg/parse_cap.py minicom.cap
python3 pg/parse_cap.py minicom.cap --limit 50       # first 50 packets
python3 pg/parse_cap.py minicom.cap --stats-only     # summary only
python3 pg/parse_cap.py minicom.cap --func 0x44      # filter by function
python3 pg/parse_cap.py minicom.cap --errors-only    # show problem packets

# Older scripts (broken — wrong checksum algorithm, kept for reference)
python3 pg/PumpIOv2.py minicom.cap
python3 pg/PumpIO.py minicom.cap
```

**Capture note:** `minicom.cap` was recorded with minicom in terminal mode, which **strips control characters** (notably `0x0B` = VT) from the byte stream. `parse_cap.py` detects and reconstructs these missing bytes automatically (labeled `CS:ART`).

## Confirmed Protocol (from reverse-engineering minicom.cap)

This is the **Jandy DLE-framed variant** of the Century EPC protocol. It differs from the official Gen3 EPC spec in several important ways:

### Packet Structure

```
10 02  [addr]  [func]  [data...]  [cs]  10 03
 ^preamble                         ^    ^postamble
                            1-byte checksum
```

- **Preamble / Postamble:** `10 02` (DLE STX) / `10 03` (DLE ETX)
- **Escape:** literal `0x10` in data is transmitted as `10 00` (DLE NUL)
- **Checksum:** `sum(0x10, 0x02, addr, func, data...) & 0xFF` — **1-byte simple sum, NOT CRC-16**
- **Baud rate:** 9600 baud, 8N1, half-duplex RS-485

### Addresses (confirmed from capture)

| Address | Role |
|---------|------|
| `0x78`  | Destination for commands sent **to** pump |
| `0x1F`  | Pump source address for Status/Sensor/Demand responses |
| `0x20`  | Pump source address for ReadID and Config responses |
| `0x01`  | Pump source address for Go/Stop ACKs |

### Function Codes and Packet Formats

Commands 0x44, 0x45, and 0x46 require leading `0x00` page/reserved bytes (hidden in minicom.cap because minicom strips NUL). Responses include dest byte `0x00`.

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

- `demand_value = RPM × 4`, stored as **little-endian 16-bit**
- Example: 2750 RPM → 11000 = `0x2AF8` → bytes `F8 2A`
- Valid range: 600–3450 RPM in 50 RPM steps (demand 2400–13800)

### Motor Status Byte (in Status response)

| Value | Meaning |
|-------|---------|
| none / `0x00` | Motor stopped |
| `0x09` | Run mode – boot (motor getting ready) |
| `0x0B` | Run mode – vector (running normally) |
| `0x20` | Fault mode – motor stopped |

### Known Quirks

1. **Checksum +5 quirk:** Some packets from the original controller (addr `0x20`, func `0x64`) have a checksum that is consistently 5 higher than the standard formula. This is a firmware quirk of the original Jandy UI, not an error.
2. **minicom strips `0x0B`:** When capturing with minicom, the byte `0x0B` (ASCII VT) is silently dropped. This affects ~1473 of 6274 packets in `minicom.cap`. `parse_cap.py` reconstructs these automatically.
3. **minicom strips `0x00`:** minicom also strips NUL bytes. This hid `0x00` page bytes in commands and `0x00` dest address in responses. See RESEARCH.md.
4. **DLE escape is `10 00` not `10 10`:** Jandy uses DLE NUL convention, confirmed by AqualinkD.

## Why CenturyVSPump Doesn't Work

The [gazoodle/CenturyVSPump](https://github.com/gazoodle/CenturyVSPump) component sends **EPC Modbus RTU** protocol: idle-time framing, CRC-16, ACK byte `0x20` in every command, 3-byte demand `[mode, lo, hi]`, address `0x15`. Our Jandy VSFloPro speaks the **Jandy DLE variant**: DLE framing (`10 02`/`10 03`), 1-byte sum checksum, NO ACK byte, 3-byte demand `[00, lo, hi]`, address `0x78`. Every layer is incompatible.

## ESPHome Config (`poolpump.yaml`)

- Uses custom local component `components/jandypump/` for Jandy DLE protocol logic
- UART pins: TX=19, RX=22, Flow control (DE/RE)=23
- Exposes to Home Assistant: on/off switch, RPM demand number (600–3450), sensors (RPM, power, current, voltage), quick-set RPM buttons
- Safety shutoff: pump stops after configurable timeout (default 120s) if HA connection lost
- Uses `source: github://kasmok/esphome-jandy-pump` for external component reference

## Key Reference Files

- `grok_chat.md` — initial protocol reverse-engineering notes, wiring diagram, Arduino sample code
- `Gen3-EPC-Modbus-Communication-Protocol-_Rev4.17.pdf` — official Century EPC protocol spec (Jandy variant differs: DLE framing, 1-byte sum checksum, different addresses)
- `minicom.cap` — captured RS-485 traffic (50,183 bytes, 6,274 packets)
- `pg/parse_cap.py` — authoritative parser, fully validated against the capture
- `RESEARCH.md` — AqualinkD cross-reference findings (missing 0x00 bytes, DLE escape convention)
- `PROTOCOL.md` — full protocol documentation with corrected packet formats

## Hardware Wiring

M5Stack ATOMIC RS485 Base screw terminals to pump connector:
- RED → 12V, GREEN → GND, BLACK (DT+) → A, YELLOW (DT-) → B
- DIP switch #1: ON (Modbus mode, enables 12V power from pump)
