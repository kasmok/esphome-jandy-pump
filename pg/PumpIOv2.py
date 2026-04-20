import sys

def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, 'little')

func_dict = {
    0x41: 'Go',
    0x42: 'Stop',
    0x43: 'Status',
    0x44: 'Set Demand',
    0x45: 'Read Sensor',
    0x46: 'Read Identification',
    0x64: 'Configuration Read/Write',
    0x65: 'Store Configuration',
}

nack_dict = {
    0x01: 'Command not recognized / illegal',
    0x02: 'Operand out of allowed range',
    0x03: 'Data out of range',
    0x04: 'General failure: fault mode',
    0x05: 'Incorrect command length',
    0x06: 'Command cannot be executed now',
    0x09: 'Buffer error (not used)',
    0x0A: 'Running parameters incomplete (not used)',
}

status_dict = {
    0x00: 'stop mode – motor stopped',
    0x09: 'run mode – boot (motor is getting ready to spin)',
    0x0B: 'run mode – vector',
    0x20: 'fault mode – motor stopped',
}

mode_dict = {
    0: 'Speed control (RPM * 4)',
    1: 'Torque control (lb-ft * 1200)',
    3: 'Reserved',
}

def print_packet(packet: bytes):
    hex_str = ' '.join(f'{b:02X}' for b in packet)
    addr = packet[2]  # Address after preamble
    func = packet[3]
    ack = packet[4]
    data = packet[5:-3]  # Exclude preamble and postamble
    direction = "controller" if ack == 0x20 else "pump"
    print(f"From {direction}: {hex_str}")

    if func & 0x80:
        orig_func = func & 0x7F
        nack = ack
        print(f"Meaning: Address 0x{addr:02X}, Error response for {func_dict.get(orig_func, 'Unknown')} (0x{orig_func:02X}), NACK: {nack_dict.get(nack, 'Unknown')} (0x{nack:02X})")
        return

    print(f"Meaning: Address 0x{addr:02X}, Function {func_dict.get(func, 'Unknown')} (0x{func:02X}), {'Request' if ack == 0x20 else 'Response (ACK)' if ack == 0x10 else f'Response (0x{ack:02X})'}")

    if func == 0x41 or func == 0x42 or func == 0x65:
        pass  # No additional data
    elif func == 0x43 and ack != 0x20:
        if len(data) > 0:
            status = data[0]
            print(f"  Status: 0x{status:02X} - {status_dict.get(status, 'Unknown')}")
    elif func == 0x44:
        if len(data) == 3:
            mode = data[0]
            dem_lo = data[1]
            dem_hi = data[2]
            demand = (dem_hi << 8) | dem_lo
            if mode == 0:
                val = demand / 4
                unit = 'RPM'
            elif mode == 1:
                val = demand / 1200
                unit = 'lb-ft'
            else:
                val = demand
                unit = '?'
            print(f"  Mode: {mode} ({mode_dict.get(mode, 'Unknown')}), Demand: {val} {unit}")
    elif func == 0x45:
        if ack == 0x20:
            if len(data) == 2:
                page = data[0]
                sens_addr = data[1]
                print(f"  Page: {page}, Sensor Address: 0x{sens_addr:02X}")
        else:
            if len(data) == 4:
                page = data[0]
                sens_addr = data[1]
                val_lo = data[2]
                val_hi = data[3]
                value = (val_hi << 8) | val_lo
                print(f"  Page: {page}, Sensor Address: 0x{sens_addr:02X}, Value: 0x{value:04X} ({value})")
                if page == 0 and sens_addr == 0x00:
                    print(f"    Interpreted: Motor Speed = {value / 4} RPM")
                elif page == 0 and sens_addr == 0x06:
                    print(f"    Interpreted: DC Bus Voltage = {value / 64} V")
    elif func == 0x46:
        if ack == 0x20:
            if len(data) == 3:
                page = data[0]
                id_addr = data[1]
                length = data[2]
                print(f"  Page: {page}, ID Address: 0x{id_addr:02X}, Length: {length} (params: {length + 1})")
        else:
            page = data[0]
            id_addr = data[1]
            length = data[2]
            id_data = data[3:]
            print(f"  Page: {page}, ID Address: 0x{id_addr:02X}, Length: {length} (params: {length + 1}), Data: {' '.join(f'{b:02X}' for b in id_data)}")
            if page == 0 and id_addr == 0x00:
                print(f"    Interpreted: Drive Software Version: {''.join(chr(b) for b in id_data if 32 <= b <= 126)}")
    elif func == 0x64:
        page_full = data[0]
        is_write = (page_full & 0x80) != 0
        page = page_full & 0x7F
        conf_addr = data[1]
        length = data[2]
        conf_data = data[3:]
        action = "Write" if is_write else "Read"
        print(f"  {action} Page: {page}, Config Address: 0x{conf_addr:02X}, Length: {length} (params: {length + 1})")
        if conf_data:
            print(f"  Data: {' '.join(f'{b:02X}' for b in conf_data)}")
            if page == 1 and conf_addr == 0x00:
                print(f"    Interpreted: Serial Timeout = {conf_data[0]} seconds")

def main(filename):
    with open(filename, 'rb') as f:
        data = f.read()

    i = 0
    while i < len(data) - 4:
        if data[i:i+2] != b'\x10\x02':  # Check for preamble
            print(data[i])
            i += 1
            continue

        addr = data[i + 2]
        func = data[i + 3]
        ack = data[i + 4]

        if func & 0x80:
            total_len = 8  # Preamble (2) + addr + func + ack + NACK + CRC (1) + postamble (2)
            if len(data) - i < total_len or data[i + total_len - 2:i + total_len] != b'\x10\x03':
                i += 1
                continue
            packet = data[i:i + total_len]
            computed_crc = crc16(packet[2:-3])  # Exclude preamble and postamble
            if computed_crc[0] == packet[-3]:  # Check LSB of CRC
                print_packet(packet)
                i += total_len
            else:
                i += 1
            continue

        if func in [0x41, 0x42, 0x65]:
            total_len = 8  # Preamble (2) + addr + func + ack + CRC (1) + postamble (2)
        elif func == 0x43:
            total_len = 8 if ack == 0x20 else 9  # +1 for status byte in response
        elif func == 0x44:
            total_len = 11  # Preamble (2) + addr + func + ack + mode + dem_lo + dem_hi + CRC (1) + postamble (2)
        elif func == 0x45:
            total_len = 10 if ack == 0x20 else 12  # Request: page + addr; Response: + val_lo + val_hi
        elif func in [0x46, 0x64]:
            if len(data) - i < 11:  # Minimum for request
                i += 1
                continue
            page = data[i + 5]
            length_val = data[i + 7]
            if ack == 0x20:  # request
                if func == 0x46:
                    data_len = 3
                else:  # 0x64
                    is_write = page & 0x80
                    if is_write:
                        data_len = length_val + 4
                    else:
                        data_len = 3
            else:  # response
                data_len = length_val + 4
            total_len = 5 + data_len + 3  # Preamble (2) + data + CRC (1) + postamble (2)
        else:
            i += 1
            continue

        if len(data) - i < total_len or data[i + total_len - 2:i + total_len] != b'\x10\x03':
            i += 1
            continue

        packet = data[i:i + total_len]
        computed_crc = crc16(packet[2:-3])  # Exclude preamble and postamble
        if computed_crc[0] == packet[-3]:  # Check LSB of CRC
            print_packet(packet)
            i += total_len
        else:
            i += 1

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python script.py <minicom.cap>")
        sys.exit(1)
    main(sys.argv[1])
