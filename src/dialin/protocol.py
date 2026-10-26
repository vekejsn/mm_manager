"""Millennium TTY frame codec and recovered challenge-response calculation.

The reference implementation and ROM research are in Dialin/display_protocol.py.
This runtime module has no dependency on the research folder or ROM files.
"""


def crc16_arc(data):
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value


def encode(command, data=b''):
    payload = bytes([command]) + bytes(data)
    if not 1 <= len(payload) <= 251:
        raise ValueError('Command plus data must be 1..251 bytes')
    body = bytes([2, len(payload)]) + payload
    return body + crc16_arc(body).to_bytes(2, 'little') + b'\x03'


def decode(packet):
    if len(packet) < 6 or packet[0] != 2 or packet[-1] != 3:
        raise ValueError('Invalid frame markers/size')
    if packet[1] < 1 or len(packet) != packet[1] + 5:
        raise ValueError('Invalid declared length')
    if crc16_arc(packet[:-3]) != int.from_bytes(packet[-3:-1], 'little'):
        raise ValueError('CRC mismatch')
    return packet[2], packet[3:-3]


def control(server_owns):
    return encode(0x84, [1 if server_owns else 0])


def coordinates(col, row):
    if type(col) is not int or type(row) is not int or not (0 <= col < 20 and 0 <= row < 2):
        raise ValueError('Coordinates must be column 0..19, row 0..1')


def printable(value):
    if not isinstance(value, str) or any(not 32 <= ord(c) <= 126 for c in value):
        raise ValueError('Text must contain printable ASCII characters only')
    return value.encode('ascii')


def text(col, row, value):
    coordinates(col, row)
    data = printable(value)
    if len(data) > 20 - col:
        raise ValueError('Text must fit within its selected row')
    return encode(0x85, bytes([col, row]) + data)


def fill(value=0x20):
    if type(value) is not int or not 0x20 <= value <= 0x7e:
        raise ValueError('Fill must be a printable ASCII byte')
    return encode(0x86, [value])


def cursor(col, row, visible=False, erase=False):
    coordinates(col, row)
    return encode(0x87, [col, row, int(bool(erase)) | int(bool(visible)) << 1])


def text_and_cursor(col, row, value, cursor_col, cursor_row, visible=False, erase=False):
    coordinates(col, row)
    coordinates(cursor_col, cursor_row)
    data = printable(value)
    if len(data) > 20 - col:
        raise ValueError('Text must fit within its selected row')
    options = int(bool(erase)) | int(bool(visible)) << 1
    return encode(0x93, bytes([cursor_col, cursor_row, options, col, row]) + data)


def auth_result(terminal_id_bcd, context_time6, destination_internal, challenge4):
    """Use ID5, raw time6, unpacked destination digits and a four-byte challenge."""
    if len(terminal_id_bcd) != 5 or len(context_time6) != 6 or len(challenge4) != 4:
        raise ValueError('Expected ID5, time6, challenge4')
    parity = context_time6[3] & 1
    digits = bytes((b & 15) if parity else (b >> 4) for b in terminal_id_bcd)
    pieces = [digits, bytes(context_time6[3:6]), bytes(destination_internal), bytes(challenge4)]
    orders = [(0, 1, 2, 3), (1, 0, 3, 2), (2, 0, 3, 1)]
    return crc16_arc(b''.join(pieces[i] for i in orders[context_time6[5] % 3]))
