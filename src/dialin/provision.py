"""Patch an explicit NOA repertory slot, preserving the other table settings.

Files are mm_manager payloads: 570 bytes for table 55; 36 for table 31.
The on-wire message-type byte is NOT present in these files.
"""
import argparse
from pathlib import Path


def pack_service_number(number):
    if not number or len(number) > 16 or any(c not in '0123456789' for c in number):
        raise ValueError('Service number must contain 1..16 decimal digits')
    digits = [int(c) or 10 for c in number]
    digits += [0] * (16 - len(digits))
    return bytes((digits[i] << 4) | digits[i + 1] for i in range(0, 16, 2))


def patch_service(data, slot, number, alias, variant, lower):
    if len(data) != 570 or not 0 <= slot <= 9 or variant not in (1, 2, 3):
        raise ValueError('Expected a 570-byte table, slot 0..9, and variant 1..3')
    if not alias or len(alias) > 20 or any(c not in '0123456789' for c in alias):
        raise ValueError('Alias must contain 1..20 decimal digits')
    if len(lower) > 20 or any(not 32 <= ord(c) <= 126 for c in lower):
        raise ValueError('Lower prompt must contain at most 20 printable ASCII characters')
    output = bytearray(data)
    start = slot * 57
    output[start + 3:start + 11] = pack_service_number(number)
    output[start + 11:start + 31] = alias.ljust(20).encode('ascii')
    output[start + 31:start + 51] = lower.ljust(20).encode('ascii')
    output[start + 51] = variant
    output[start + 55] |= 0x10
    return bytes(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--table55', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--slot', type=int, required=True)
    parser.add_argument('--number', required=True)
    parser.add_argument('--alias', required=True)
    parser.add_argument('--variant', type=int, choices=(1, 2, 3), default=2)
    parser.add_argument('--prompt', default='ONLINE SERVICES')
    parser.add_argument('--table31', type=Path)
    parser.add_argument('--output31', type=Path)
    parser.add_argument('--tx-delay', type=int)
    parser.add_argument('--rx-gap', type=int)
    args = parser.parse_args()
    try:
        original = args.table55.read_bytes()
        updated = patch_service(original, args.slot, args.number, args.alias, args.variant, args.prompt)
        if args.output.exists() or args.output.resolve() == args.table55.resolve():
            raise ValueError('Use a new output path; input tables are preserved')
        table31 = None
        if args.table31:
            table31 = bytearray(args.table31.read_bytes())
            if len(table31) != 36 or not args.output31 or args.output31.exists():
                raise ValueError('Table 31 needs a 36-byte input and a new --output31 path')
            if args.output31.resolve() == args.output.resolve():
                raise ValueError('Table outputs must be different paths')
            for offset, value in ((10, args.tx_delay), (11, args.rx_gap)):
                if value is not None:
                    if not 0 <= value <= 255:
                        raise ValueError('Packet delays must be 0..255 ticks (10ms each)')
                    table31[offset] = value
        elif args.output31 or args.tx_delay is not None or args.rx_gap is not None:
            raise ValueError('Table 31 edits require --table31 and --output31')
        with args.output.open('xb') as output:
            output.write(updated)
        if table31 is not None:
            with args.output31.open('xb') as output:
                output.write(table31)
        print(f'Patched slot {args.slot}: {original[args.slot*57:(args.slot+1)*57].hex()}')
        print(f'New service alias {args.alias} -> {args.number}; variant {args.variant}')
        print('Existing payment/classification fields and table-31 routing bitmap preserved.')
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
