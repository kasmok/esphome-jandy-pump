# Research: Missing 0x00 Page Bytes in Jandy ePump Protocol

**Date:** 2026-04-21
**Status:** Root cause identified — code fix pending

## Summary

Cross-referencing our minicom.cap analysis with the AqualinkD project's independently captured ePump protocol data revealed that **all data-bearing commands (0x44, 0x45, 0x46) require a leading `0x00` "page/reserved" byte** that was invisible in our minicom captures because **minicom strips NUL (0x00) bytes** from the data stream, just as it strips `0x0B` (VT).

This is the root cause of all NACK 0x03 ("data out of range") errors for Set Demand, Read Sensor, and ReadID commands. Go, Stop, and Status work because they have no data payload — nothing for minicom to strip.

## Evidence

### Source: AqualinkD (aqualinkd/AqualinkD)

AqualinkD is a mature open-source project that controls Jandy pool equipment via RS-485. It captures data through its own RS-485 interface (not minicom), providing unfiltered protocol data. The project has a dedicated [JANDY_RS485_PROTOCOL.md](https://github.com/aqualinkd/AqualinkD/blob/master/JANDY_RS485_PROTOCOL.md) and extensive packet captures in [source/epump.h](https://github.com/aqualinkd/AqualinkD/blob/master/source/epump.h).

### Packet Comparison

#### Set Demand (0x44) — Set RPM to 2520

| Source | Raw packet |
|--------|-----------|
| AqualinkD (correct) | `10 02 78 44 00 60 27 55 10 03` |
| Our code (broken) | `10 02 78 44 60 27 55 10 03` |
| minicom.cap (stripped) | `10 02 78 44 60 27 55 10 03` |

The `0x00` after `0x44` is a required "page/reserved" byte. Payload is `00 60 27`:
- `0x00` = page/reserved (always 0x00)
- `0x60 0x27` = demand value little-endian = 0x2760 = 10080 / 4 = **2520 RPM**

Checksum verification: `(0x10 + 0x02 + 0x78 + 0x44 + 0x00 + 0x60 + 0x27) & 0xFF = 0x55` -- matches the AqualinkD packet.

#### Read Sensor (0x45) — Read Watts (sensor 0x05)

| Source | Raw packet |
|--------|-----------|
| AqualinkD (correct) | `10 02 78 45 00 05 D4 10 03` |
| Our code (broken) | `10 02 78 45 05 D4 10 03` |
| minicom.cap (stripped) | `10 02 78 45 05 D4 10 03` |

Payload is `00 05`:
- `0x00` = page (always 0x00 for standard sensors)
- `0x05` = sensor address (watts)

Checksum: `(0x10 + 0x02 + 0x78 + 0x45 + 0x00 + 0x05) & 0xFF = 0xD4` -- matches.

#### ReadID (0x46) — Read page 3

| Source | Raw packet |
|--------|-----------|
| AqualinkD (correct) | `10 02 78 46 00 00 03 D3 10 03` |
| Our code (broken) | `10 02 78 46 03 D3 10 03` |
| minicom.cap (stripped) | `10 02 78 46 03 D3 10 03` |

Payload is `00 00 03`:
- `0x00` = reserved
- `0x00` = reserved
- `0x03` = page number

Checksum: `(0x10 + 0x02 + 0x78 + 0x46 + 0x00 + 0x00 + 0x03) & 0xFF = 0xD3` -- matches.

### Why Checksums Still Validated

`0x00` added to any sum is still the same sum. This is why our minicom.cap parser showed 100% valid checksums even with the missing bytes — there was **no way to detect the missing 0x00 bytes from checksum analysis alone**.

### Why Go/Stop/Status Work

These commands have **no data payload**:
- Go: `78 41 [cs]` — nothing to strip
- Stop: `78 42 [cs]` — nothing to strip
- Status: `78 43 [cs]` — nothing to strip

### Responses Also Have Extra 0x00 Bytes

AqualinkD's captures show responses include a **destination address byte `0x00`** before the source address:

```
AqualinkD response:  10 02 00 1F 44 00 60 27 00 FC 10 03
                           ^^ ^^
                           dest src
Our observation:     10 02 00 1F 44 60 27 7F 10 03
                           ^^
                           "leading null" we were stripping
```

The "leading null byte" we observed in live testing (Finding 1 in PROTOCOL.md) was actually the **destination address (0x00 = master)** — a normal part of the protocol that minicom stripped from the original capture. Our workaround of "stripping leading nulls" was accidentally correct but for the wrong reason.

## AqualinkD Poll Cycle (Confirmed Working)

From `source/epump.h`, the AquaLink RS controller sends this repeating cycle every ~5 seconds:

```
1. Set Demand (0x44):  78 44 00 [dem_lo] [dem_hi]    → response echoes demand back
2. Go (0x41):          78 41                          → ACK
3. Read Watts (0x45):  78 45 00 05                    → watts value
4. Status (0x43):      78 43                          → status + padding
5. ReadID (0x46):      78 46 00 00 03                 → identification data
```

This is a simpler cycle than what we observed in minicom.cap (which was the JEP-R standalone controller). The AquaLink RS system doesn't send Config (0x64) or bare Read Sensor in its normal cycle.

## Additional Findings

### DLE Escape Convention: `10 00` not `10 10`

From AqualinkD's [JANDY_RS485_PROTOCOL.md](https://github.com/aqualinkd/AqualinkD/blob/master/JANDY_RS485_PROTOCOL.md):

> "If the value 0x10 (DLE) appears in the data portion of the packet (after STX and before the final DLE), it must be escaped by inserting a NUL byte (0x00) after it."

So a literal `0x10` in data is transmitted as `10 00`, not `10 10` (standard DLE convention). Our TX code and RX state machine both need updating.

**Impact:** This matters when demand values or sensor addresses contain `0x10`. For example:
- 1024 RPM = demand 4096 = `0x1000` → bytes `00 10` → must be escaped as `00 10 00`
- This is an edge case but will cause protocol errors at certain RPM values

### DIP Switch Configuration (from ePump Installation Manual)

| Switches | Setting | Mode |
|----------|---------|------|
| SW1 + SW2 | Both ON | JEP-R standalone controller mode |
| SW1 + SW2 | Both OFF | AquaLink RS / PDA / Z4 automation mode |
| SW3 + SW4 | Both OFF | Pump address 0x78 (Pump 1) |
| SW5 (Century motors only) | ON | Jandy protocol |
| SW5 (Century motors only) | OFF | Modbus protocol |

Our pump likely has SW1+SW2=ON (it was paired with a JEP-R controller) and SW3+SW4=OFF (address 0x78). The protocol is the same in both JEP-R and AquaLink modes — only the controller behavior differs.

### Timing Requirements

From [AqualinkD Discussion #216](https://github.com/aqualinkd/AqualinkD/discussions/216):
- Controller must respond within **60ms** or the pump assumes the device is dead
- RS-485 command must be sent at least **once per minute** or the interface times out and the motor shuts off
- Our ESPHome update interval of 2-5 seconds is well within the 1-minute timeout

### Response Format Details

From AqualinkD's response parsing (`source/devices_jandy.c`):

**Set Demand (0x44) response:**
```
10 02 [dest=00] [src=1F] [func=44] [rsv=00] [dem_lo] [dem_hi] [extra=00] [cs] 10 03
```

**Read Watts (0x45) response:**
```
10 02 [dest=00] [src=1F] [func=45] [rsv=00] [sensor] [val_lo] [val_hi] [cs] 10 03
```

**Status (0x43) response:**
```
10 02 [dest=00] [src=1F] [func=43] [status] [pad] [pad] [pad] [cs] 10 03
```

**Go/Stop ACK response:**
```
10 02 [dest=00] [src=01] [func=41/42] [extra=00] [cs] 10 03
```

## Required Code Changes

### 1. Add 0x00 page byte to commands (CRITICAL)

- `create_set_demand_command()`: payload = `{0x00, dem_lo, dem_hi}` (was `{dem_lo, dem_hi}`)
- `create_read_sensor_command()`: payload = `{0x00, sensor_addr}` (was `{sensor_addr}`)
- ReadID in `update()`: payload = `{0x00, 0x00, page}` (was `{page}`)
- "Bare Read Sensor" was actually `{0x00}` (page byte only, no sensor), not empty

### 2. Fix DLE escape (IMPORTANT)

TX: `10 10` -> `10 00` (in `send_jandy_raw()`)
RX: Accept `10 00` as escaped DLE in `process_rx_byte_()` DLE_ESCAPE state

### 3. Update response parsing (MODERATE)

Responses have `[dest=0x00]` before `[src]`. Our "strip leading nulls" workaround handles this, but we should properly account for the destination address byte instead of treating it as junk.

### 4. Update poll cycle (MINOR)

Consider adopting AqualinkD's simpler cycle:
```
Set Demand → Go → Read Sensor (watts) → Status → ReadID
```
instead of mimicking the JEP-R's more complex startup sequence. The init handshake (Config/ReadID before sensors) may not be needed once commands have the correct format.

## Sources

- **AqualinkD JANDY_RS485_PROTOCOL.md** — https://github.com/aqualinkd/AqualinkD/blob/master/JANDY_RS485_PROTOCOL.md
  Protocol documentation including ePump command formats, DLE escaping, and packet offsets.

- **AqualinkD source/epump.h** — https://github.com/aqualinkd/AqualinkD/blob/master/source/epump.h
  Annotated packet captures from real AquaLink RS ↔ ePump communication, with byte-level analysis.

- **AqualinkD source/devices_jandy.c** — https://github.com/aqualinkd/AqualinkD/blob/master/source/devices_jandy.c
  Response parsing code showing byte offsets for RPM, Watts, and status extraction.

- **AqualinkD source/aq_serial.h** — https://github.com/aqualinkd/AqualinkD/blob/master/source/aq_serial.h
  Protocol constants: ePump addresses 0x78-0x7B, command codes CMD_EPUMP_RPM=0x44, CMD_EPUMP_WATTS=0x45.

- **AqualinkD Discussion #365** — https://github.com/aqualinkd/AqualinkD/discussions/365
  Confirms pump operates as slave on isolated RS-485 bus; master must poll within timing constraints.

- **AqualinkD Discussion #216** — https://github.com/aqualinkd/AqualinkD/discussions/216
  60ms response window requirement; timing constraints for RS-485 communication.

- **CenturyVSPump ESPHome component** — https://github.com/gazoodle/CenturyVSPump
  Modbus RTU variant with 0x20 ACK byte and CRC-16 (incompatible with Jandy DLE, but useful structural reference).

- **TroubleFreePool: Century VGreen motor automation** — https://www.troublefreepool.com/threads/century-regal-vgreen-motor-automation.238733/
  DIP switch SW5 controls Jandy vs Modbus protocol mode; motor must be power-cycled after switch change.

- **Zodiac ePump Installation Manual** — https://grandvistapools.com/wp-content/uploads/2014/06/Epump-Manual.pdf
  DIP switch 1+2 ON = JEP-R mode, OFF = AquaLink mode; DIP 3+4 for pump address selection.
