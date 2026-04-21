#!/usr/bin/env python3
"""
Pool pump RS-485 capture parser for Jandy/Zodiac ePump (Century VSPump) protocol.

Jandy variant protocol uses DLE framing (NOT standard Modbus RTU):
  Preamble:  10 02  (DLE STX)
  Postamble: 10 03  (DLE ETX)
  Escape:    10 10  in data stream = literal 0x10 data byte
  Checksum:  sum(0x10, 0x02, addr, func, data...) & 0xFF  (1 byte, NOT CRC-16)

Usage:
  python3 pg/parse_cap.py minicom.cap
  python3 pg/parse_cap.py minicom.cap --limit 50
  python3 pg/parse_cap.py minicom.cap --stats-only
"""

import sys
import argparse

# ─── Protocol Tables ────────────────────────────────────────────────────────

FUNC_NAMES = {
    0x41: 'Go',
    0x42: 'Stop',
    0x43: 'Status',
    0x44: 'Set Demand',
    0x45: 'Read Sensor',
    0x46: 'Read Identification',
    0x64: 'Config Read/Write',
    0x65: 'Store Configuration',
}

# Address roles
ADDR_ROLE = {
    0x78: ('CTRL→PUMP', 'Controller sending command to pump'),
    0x1F: ('PUMP→CTRL', 'Pump sending Status/Sensor/Demand response'),
    0x20: ('PUMP→CTRL', 'Pump sending ID/Config response'),
    0x01: ('PUMP→CTRL', 'Pump sending Go/Stop ACK'),
    0xFF: ('PUMP→CTRL', 'Pump NACK (error/rejection)'),
}

# Status byte (from Status command response, and Sensor page 0 addr 0x08)
STATUS_BYTE = {
    0x00: 'stopped',
    0x09: 'run-boot (motor getting ready to spin)',
    0x0B: 'run-vector (running normally)',
    0x20: 'fault (motor stopped)',
    0x23: 'run (extended status)',
}

# Sensor Page 0 addressing map (Appendix A, Table 3)
SENSOR_P0 = {
    0x00: ('Motor Speed',        lambda v: f'{v/4:.0f} RPM'),
    0x01: ('Motor Current',      lambda v: f'{v/1000:.3f} A'),
    0x02: ('Operating Mode',     lambda v: 'speed-ctrl' if v==0 else 'torque-ctrl' if v==1 else f'0x{v:02X}'),
    0x03: ('Demand',             lambda v: f'{v/4:.0f} RPM (demand={v})'),
    0x04: ('Torque',             lambda v: f'{v/1200:.3f} lb-ft'),
    0x05: ('Inverter Input Pwr', lambda v: f'{v} W'),
    0x06: ('DC Bus Voltage',     lambda v: f'{v/64:.1f} V'),
    0x07: ('Ambient Temp',       lambda v: f'{v/128:.1f} °C'),
    0x08: ('Status',             lambda v: STATUS_BYTE.get(v, f'0x{v:02X}')),
    0x09: ('Previous Fault',     lambda v: f'fault code 0x{v:02X}'),
    0x0A: ('Output Power',       lambda v: f'{v} W'),
    0x0B: ('SVRS Bypass Status', lambda v: f'0x{v:02X}'),
    0x0C: ('Num Current Faults', lambda v: f'{v}'),
    0x0E: ('Ramp Status',        lambda v: f'0x{v:02X}'),
    0x0F: ('Num Total Faults',   lambda v: f'{v}'),
    0x10: ('Prime Status',       lambda v: {0:'priming-stopped',1:'priming-running',2:'priming-over'}.get(v, f'0x{v:02X}')),
    0x12: ('IGBT Temperature',   lambda v: f'{v/128:.1f} °C'),
    0x14: ('External Input Status', lambda v: f'0x{v:02X}'),
    0x15: ('Reference Speed',    lambda v: f'{v/4:.0f} RPM'),
}

# Sensor Page 1 addressing map (Appendix A, Table 4)
SENSOR_P1 = {
    0x07: ('Serial Timeout Counter', lambda v: f'{v}'),
    0x08: ('Total Run Time Low',     lambda v: f'{v} hrs'),
    0x09: ('Total Run Time High',    lambda v: f'{v} hrs'),
    0x0A: ('Total Life Time Low',    lambda v: f'{v} hrs'),
    0x0B: ('Total Life Time High',   lambda v: f'{v} hrs'),
    0x15: ('1st Active Fault',       lambda v: f'fault 0x{v:02X}'),
    0x16: ('2nd Active Fault',       lambda v: f'fault 0x{v:02X}'),
    0x17: ('3rd Active Fault',       lambda v: f'fault 0x{v:02X}'),
    0x18: ('4th Active Fault',       lambda v: f'fault 0x{v:02X}'),
}

SENSOR_PAGES = {0: SENSOR_P0, 1: SENSOR_P1}

NACK_CODES = {
    0x01: 'Command not recognized / illegal',
    0x02: 'Operand out of allowed range',
    0x03: 'Data out of range',
    0x04: 'General failure: fault mode',
    0x05: 'Incorrect command length',
    0x06: 'Command cannot be executed now',
}

# ─── Packet Extraction ───────────────────────────────────────────────────────

def extract_packets(raw: bytes) -> list:
    """
    Extract DLE-framed packets from raw byte stream.
    Returns list of (offset, logical_inner_bytes) tuples.
    logical_inner_bytes has 10 10 unescaped to single 0x10.
    """
    packets = []
    i = 0
    skipped = 0
    while i < len(raw) - 1:
        if raw[i] == 0x10 and raw[i+1] == 0x02:
            start = i
            j = i + 2
            buf = bytearray()
            found_end = False
            while j < len(raw) - 1:
                if raw[j] == 0x10:
                    nxt = raw[j+1]
                    if nxt == 0x03:
                        found_end = True
                        i = j + 2
                        break
                    elif nxt == 0x02:
                        # New frame start within frame — current frame corrupt
                        break
                    elif nxt == 0x10:
                        buf.append(0x10)  # unescape DLE DLE → 0x10
                        j += 2
                        continue
                    else:
                        buf.append(raw[j])
                        j += 1
                else:
                    buf.append(raw[j])
                    j += 1
            if found_end:
                packets.append((start, bytes(buf)))
            else:
                skipped += 1
                i = start + 1
        else:
            skipped += 1
            i += 1
    return packets, skipped

# ─── Checksum Validation ─────────────────────────────────────────────────────

# Control characters that minicom strips from captures (terminal artifact)
# These bytes vanish from the .cap file, causing apparent checksum mismatches.
# The "diff" (actual_cs - expected_cs) equals the stripped byte's value.
MINICOM_STRIPPED = {
    0x07: 'BEL',  0x08: 'BS',   0x09: 'HT',   0x0A: 'LF',
    0x0B: 'VT',   0x0C: 'FF',   0x0D: 'CR',   0x0E: 'SO',
    0x0F: 'SI',   0x11: 'XON',  0x13: 'XOFF',
}


def verify_checksum(inner: bytes) -> tuple:
    """
    Returns (status, expected_cs, actual_cs, diff, reconstructed_inner)

    status values:
      'ok'      — checksum matches exactly
      'quirk5'  — off by +5 (known firmware quirk of original controller)
      'artifact'— off by a value matching a control char stripped by minicom;
                  reconstructed_inner has the missing byte re-inserted
      'bad'     — checksum mismatch with no known explanation

    Checksum = sum(0x10, 0x02, addr, func, data_bytes...) & 0xFF
    """
    if len(inner) < 2:
        return 'bad', 0, 0, 0, inner

    actual_cs = inner[-1]
    payload = inner[:-1]
    expected_cs = (0x10 + 0x02 + sum(payload)) & 0xFF
    diff = (actual_cs - expected_cs) & 0xFF

    if diff == 0:
        return 'ok', expected_cs, actual_cs, 0, inner

    if diff == 5:
        return 'quirk5', expected_cs, actual_cs, 5, inner

    if diff in MINICOM_STRIPPED:
        # Try to reconstruct: the missing byte (= diff) was somewhere in the data.
        # Strategy: insert it at each possible position and pick the one that
        # produces a valid checksum AND makes semantic sense (best-effort).
        dropped = diff
        # For most cases, the dropped byte appeared just before the checksum,
        # as the last data byte. Try all positions and take the last valid one
        # (closer to checksum = most common for trailing value bytes).
        best = None
        for pos in range(len(payload) + 1):
            candidate = bytes(list(payload[:pos]) + [dropped] + list(payload[pos:]))
            cs_check = (0x10 + 0x02 + sum(candidate)) & 0xFF
            if cs_check == actual_cs:
                best = bytes(list(candidate) + [actual_cs])
                break  # first valid position; for data bytes, use last valid instead
        # For sensor/status data, the dropped byte is usually a DATA byte, not
        # addr/func — so prefer insertion AFTER func (index 2+).
        # Re-scan from the right for a more semantically likely position.
        last_valid = None
        for pos in range(len(payload), 1, -1):  # try from end toward func
            candidate = bytes(list(payload[:pos]) + [dropped] + list(payload[pos:]))
            cs_check = (0x10 + 0x02 + sum(candidate)) & 0xFF
            if cs_check == actual_cs:
                last_valid = bytes(list(candidate) + [actual_cs])
                break
        reconstructed = last_valid if last_valid else best
        if reconstructed:
            return 'artifact', expected_cs, actual_cs, diff, reconstructed

    return 'bad', expected_cs, actual_cs, diff, inner

# ─── Packet Decoder ──────────────────────────────────────────────────────────

def decode_sensor(sensor_addr: int, val_lo: int, val_hi: int) -> str:
    """Decode a sensor page/address + 16-bit value into human-readable string."""
    # Determine page from high nibble of sensor_addr (Jandy packs page+addr in 1 byte)
    page = (sensor_addr >> 4) & 0x0F
    addr = sensor_addr & 0x0F if page > 0 else sensor_addr
    # If high nibble is 0, it's page 0 with the full byte as address
    if page == 0:
        page = 0
        addr = sensor_addr

    value = (val_hi << 8) | val_lo
    page_map = SENSOR_PAGES.get(page, {})
    if addr in page_map:
        name, fmt = page_map[addr]
        return f'{name} = {fmt(value)}'
    return f'page {page} addr 0x{addr:02X} = 0x{value:04X} ({value})'


def decode_packet(inner: bytes) -> list:
    """
    Decode inner bytes (addr, func, data..., cs) into a list of description strings.
    Returns list of lines to print.
    """
    lines = []
    if len(inner) < 3:
        lines.append(f'  [too short to decode: {len(inner)} bytes]')
        return lines

    addr = inner[0]
    func = inner[1]
    data = inner[2:-1]   # everything between func and checksum
    cs   = inner[-1]

    # Direction
    role, role_desc = ADDR_ROLE.get(addr, (f'0x{addr:02X}', f'unknown device 0x{addr:02X}'))
    is_cmd = (addr == 0x78)
    direction = 'CMD ' if is_cmd else 'RESP'

    func_name = FUNC_NAMES.get(func, f'UNKNOWN(0x{func:02X})')
    lines.append(f'  {direction}  addr=0x{addr:02X} ({role})  func=0x{func:02X} ({func_name})')

    # NACK: addr=0xFF means pump rejected the command
    if addr == 0xFF:
        nack = data[0] if data else 0
        nack_desc = NACK_CODES.get(nack, f'unknown error 0x{nack:02X}')
        func_name_short = FUNC_NAMES.get(func, f'0x{func:02X}')
        lines.append(f'  ← NACK for {func_name_short}: code 0x{nack:02X} — {nack_desc}')
        return lines

    # Error reply: MSB of func set (EPC Modbus style, not typically seen in Jandy DLE)
    if func & 0x80:
        orig_func = func & 0x7F
        orig_name = FUNC_NAMES.get(orig_func, f'0x{orig_func:02X}')
        nack = data[0] if data else 0
        nack_desc = NACK_CODES.get(nack, f'unknown error 0x{nack:02X}')
        lines.append(f'  ERROR reply for {orig_name}: NACK 0x{nack:02X} — {nack_desc}')
        return lines

    # ── Go (0x41) ──
    if func == 0x41:
        if is_cmd:
            lines.append('  → START PUMP at previously set demand speed')
        else:
            lines.append('  ← ACK: pump acknowledged Go command')

    # ── Stop (0x42) ──
    elif func == 0x42:
        if is_cmd:
            lines.append('  → STOP PUMP')
        else:
            lines.append('  ← ACK: pump acknowledged Stop command')

    # ── Status (0x43) ──
    elif func == 0x43:
        if is_cmd:
            lines.append('  → Query pump status')
        else:
            if len(data) == 0:
                lines.append('  ← Status: motor STOPPED (no status byte = stopped/0x00)')
            elif len(data) == 1:
                s = data[0]
                desc = STATUS_BYTE.get(s, f'unknown 0x{s:02X}')
                lines.append(f'  ← Status: 0x{s:02X} — {desc}')
            else:
                lines.append(f'  ← Status: (unexpected data length {len(data)}): {data.hex()}')

    # ── Set Demand (0x44) ──
    elif func == 0x44:
        if len(data) >= 2:
            demand = (data[1] << 8) | data[0]
            rpm = demand / 4
            if is_cmd:
                lines.append(f'  → SET DEMAND: {rpm:.0f} RPM  (demand={demand} = 0x{demand:04X}, bytes {data[0]:02X} {data[1]:02X})')
            else:
                lines.append(f'  ← ACK Set Demand: {rpm:.0f} RPM  (echoed demand={demand})')
            if len(data) > 2:
                lines.append(f'    extra data: {" ".join(f"{b:02X}" for b in data[2:])}')
        else:
            lines.append(f'  Set Demand: (too few data bytes: {data.hex()})')

    # ── Read Sensor (0x45) ──
    elif func == 0x45:
        if is_cmd:
            if len(data) == 0:
                lines.append('  → Read Sensor: poll (no sensor address specified)')
            elif len(data) == 1:
                saddr = data[0]
                page = 0
                page_map = SENSOR_PAGES.get(page, {})
                sname = page_map[saddr][0] if saddr in page_map else f'sensor 0x{saddr:02X}'
                lines.append(f'  → Read Sensor: page {page} addr 0x{saddr:02X} ({sname})')
            else:
                lines.append(f'  → Read Sensor: {" ".join(f"{b:02X}" for b in data)}')
        else:
            if len(data) == 0:
                lines.append('  ← Read Sensor: ACK (no value)')
            elif len(data) == 1:
                saddr = data[0]
                lines.append(f'  ← Read Sensor: ACK sensor 0x{saddr:02X} (no value yet)')
            elif len(data) == 3:
                saddr = data[0]
                val_lo, val_hi = data[1], data[2]
                value = (val_hi << 8) | val_lo
                decoded = decode_sensor(saddr, val_lo, val_hi)
                lines.append(f'  ← Read Sensor: {decoded}  (raw 0x{value:04X})')
            elif len(data) >= 4:
                # Multi-sensor read
                lines.append(f'  ← Read Sensor (multi): {" ".join(f"{b:02X}" for b in data)}')
                i = 0
                while i + 2 < len(data):
                    saddr = data[i]
                    val_lo, val_hi = data[i+1], data[i+2]
                    value = (val_hi << 8) | val_lo
                    decoded = decode_sensor(saddr, val_lo, val_hi)
                    lines.append(f'    [{i//3}] {decoded}')
                    i += 3
            else:
                lines.append(f'  ← Read Sensor: data={data.hex()}')

    # ── Read Identification (0x46) ──
    elif func == 0x46:
        if is_cmd:
            if len(data) >= 1:
                page = data[0]
                lines.append(f'  → Read Identification: page {page}')
                if len(data) >= 2:
                    lines.append(f'    addr=0x{data[1]:02X}, length={data[2] if len(data)>2 else "?"}')
            else:
                lines.append('  → Read Identification')
        else:
            if len(data) >= 1:
                printable = ''.join(chr(b) if 32 <= b <= 126 else f'\\x{b:02X}' for b in data)
                ascii_only = ''.join(chr(b) for b in data if 32 <= b <= 126)
                lines.append(f'  ← Identification data: "{ascii_only}"  (raw: {" ".join(f"{b:02X}" for b in data)})')
            else:
                lines.append('  ← Identification: (no data)')

    # ── Config Read/Write (0x64) ──
    elif func == 0x64:
        if is_cmd:
            if len(data) >= 1:
                page_byte = data[0]
                is_write = bool(page_byte & 0x80)
                page = page_byte & 0x7F
                action = 'WRITE' if is_write else 'READ'
                lines.append(f'  → Config {action}: page {page}')
                if len(data) >= 2:
                    lines.append(f'    conf_addr=0x{data[1]:02X}' + (f', length={data[2]}' if len(data) > 2 else ''))
                if is_write and len(data) > 3:
                    lines.append(f'    write data: {" ".join(f"{b:02X}" for b in data[3:])}')
            else:
                lines.append('  → Config Read/Write (no page specified)')
        else:
            if len(data) >= 1:
                page = data[0] & 0x7F
                lines.append(f'  ← Config response: page {page}')
                if len(data) > 1:
                    conf_data = data[1:]
                    printable = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in conf_data)
                    lines.append(f'    data ({len(conf_data)} bytes): {" ".join(f"{b:02X}" for b in conf_data)}  "{printable}"')
            else:
                lines.append('  ← Config: (no data)')

    # ── Store Configuration (0x65) ──
    elif func == 0x65:
        if is_cmd:
            lines.append('  → Store Configuration to flash')
        else:
            lines.append('  ← ACK: configuration stored')

    else:
        lines.append(f'  Unknown function 0x{func:02X}: data={data.hex() if data else "(none)"}')

    return lines


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Parse Jandy ePump RS-485 minicom capture')
    parser.add_argument('filename', help='Capture file (e.g. minicom.cap)')
    parser.add_argument('--limit', type=int, default=0, help='Only show first N packets (0=all)')
    parser.add_argument('--stats-only', action='store_true', help='Print summary stats only')
    parser.add_argument('--errors-only', action='store_true', help='Only show checksum-error packets')
    parser.add_argument('--func', type=lambda x: int(x,0), default=None,
                        help='Filter to one function code, e.g. --func 0x44')
    args = parser.parse_args()

    with open(args.filename, 'rb') as f:
        raw = f.read()

    packets, skipped_bytes = extract_packets(raw)

    # ── Stats accumulators ──
    stats = {
        'total': len(packets),
        'cs_ok': 0,
        'cs_quirk': 0,    # off by +5 (known firmware quirk of original controller)
        'cs_artifact': 0, # control char stripped by minicom, reconstructed
        'cs_bad': 0,
        'by_func': {},
        'by_addr': {},
        'demand_values': set(),
        'status_values': {},
    }

    if not args.stats_only:
        print(f'Parsing {args.filename}  ({len(raw)} bytes raw, {len(packets)} packets found)')
        print('─' * 80)

    shown = 0
    for pkt_num, (offset, inner) in enumerate(packets, 1):
        if len(inner) < 3:
            continue

        addr = inner[0]
        func = inner[1]

        # Always accumulate stats for ALL packets
        stats['by_func'][func] = stats['by_func'].get(func, 0) + 1
        stats['by_addr'][addr] = stats['by_addr'].get(addr, 0) + 1

        cs_status, expected_cs, actual_cs, diff, decode_inner = verify_checksum(inner)
        if cs_status == 'ok':
            stats['cs_ok'] += 1
            cs_label = 'CS:OK  '
        elif cs_status == 'quirk5':
            stats['cs_quirk'] += 1
            cs_label = 'CS:Q+5 '
            decode_inner = inner
        elif cs_status == 'artifact':
            stats['cs_artifact'] += 1
            dropped_name = MINICOM_STRIPPED.get(diff, f'0x{diff:02X}')
            cs_label = f'CS:ART({dropped_name} stripped)'
        else:
            stats['cs_bad'] += 1
            cs_label = f'CS:BAD (got {actual_cs:02X} want {expected_cs:02X})'
            decode_inner = inner

        # Use reconstructed inner for stats and decode when available
        d_inner = decode_inner  # may have missing byte re-inserted

        if func == 0x44 and addr == 0x78:
            data = d_inner[2:-1]
            if len(data) >= 2:
                demand = (data[1] << 8) | data[0]
                stats['demand_values'].add(demand // 4)

        if func == 0x43 and addr == 0x1F:
            data = d_inner[2:-1]
            if len(data) == 1:
                s = data[0]
                stats['status_values'][s] = stats['status_values'].get(s, 0) + 1

        # Apply display filters
        if args.limit and shown >= args.limit:
            continue
        if args.errors_only and cs_status == 'ok':
            continue
        if args.func is not None and func != args.func:
            continue
        if args.stats_only:
            continue

        # ── Format raw hex: show original bytes, mark reconstructed bytes ──
        raw_hex = ' '.join(f'{b:02X}' for b in ([0x10, 0x02] + list(inner) + [0x10, 0x03]))
        if cs_status == 'artifact' and d_inner != inner:
            recon_hex = ' '.join(f'{b:02X}' for b in ([0x10, 0x02] + list(d_inner) + [0x10, 0x03]))
        else:
            recon_hex = None

        # ── Print packet ──
        print(f'[{pkt_num:05d}] @0x{offset:05X}  {cs_label}')
        print(f'  RAW: {raw_hex}')
        if recon_hex:
            print(f'  REC: {recon_hex}  ← reconstructed (re-inserted stripped byte 0x{diff:02X})')
        decode_lines = decode_packet(d_inner)
        for line in decode_lines:
            print(line)
        print()
        shown += 1

    # ── Summary Stats ──
    print('=' * 80)
    print('SUMMARY')
    print('=' * 80)
    print(f'  Raw bytes      : {len(raw):,}')
    print(f'  Skipped bytes  : {skipped_bytes:,}  (not inside valid frames)')
    print(f'  Total packets  : {stats["total"]:,}')
    print(f'  Checksum OK    : {stats["cs_ok"]:,}')
    print(f'  Checksum +5    : {stats["cs_quirk"]:,}  (firmware quirk: Config cmds + addr 0x20 responses)')
    print(f'  Checksum ART   : {stats["cs_artifact"]:,}  (minicom stripped a control char; packet reconstructed)')
    print(f'  Checksum bad   : {stats["cs_bad"]:,}  (genuinely corrupted / partial packets)')
    print()

    print('  Packets by address:')
    for addr, cnt in sorted(stats['by_addr'].items()):
        role, desc = ADDR_ROLE.get(addr, (f'0x{addr:02X}', 'unknown'))
        print(f'    0x{addr:02X}  {cnt:5d}  {desc}')

    print()
    print('  Packets by function:')
    for func, cnt in sorted(stats['by_func'].items()):
        name = FUNC_NAMES.get(func, f'UNKNOWN')
        print(f'    0x{func:02X}  {cnt:5d}  {name}')

    if stats['demand_values']:
        print()
        print('  RPM demand values seen:')
        rpms = sorted(stats['demand_values'])
        rpm_str = ', '.join(f'{r:.0f}' for r in rpms)
        print(f'    {rpm_str} RPM')

    if stats['status_values']:
        print()
        print('  Motor status values seen (in Status responses):')
        for s, cnt in sorted(stats['status_values'].items()):
            desc = STATUS_BYTE.get(s, f'unknown 0x{s:02X}')
            print(f'    0x{s:02X}  {cnt:4d}x  {desc}')

    print()
    print('Protocol notes:')
    print('  Jandy variant: DLE framing (10 02 ... 10 03), 1-byte sum checksum (NOT CRC-16)')
    print('  Addresses: 0x78=cmd-to-pump, 0x1F=pump-response, 0x20=pump-ID-response, 0x01=pump-ack')
    print('  Demand encoding: value = RPM * 4, little-endian 16-bit')


if __name__ == '__main__':
    main()
