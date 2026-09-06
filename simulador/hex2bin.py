#!/usr/bin/env python3
"""Conversor Intel-Hex (formato gerado por OH166/L166 da Keil) -> .bin cru
para carregar no c166sim.py (que só entende .bin com endereço físico = offset
do arquivo, ver README.md).

Uso: python3 hex2bin.py entrada.h86 saida.bin
"""
import sys


def hex_to_bin(path, fill=0xFF):
    ext_addr = 0
    chunks = {}  # endereço absoluto -> bytes
    max_end = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith(':'):
                continue
            raw = bytes.fromhex(line[1:])
            length = raw[0]
            addr16 = (raw[1] << 8) | raw[2]
            rectype = raw[3]
            data = raw[4:4 + length]
            # (ignora checksum, byte final)
            if rectype == 0x00:  # data
                abs_addr = ext_addr + addr16
                chunks[abs_addr] = data
                max_end = max(max_end, abs_addr + len(data))
            elif rectype == 0x01:  # EOF
                break
            elif rectype == 0x02:  # extended segment address (<<4)
                ext_addr = ((data[0] << 8) | data[1]) << 4
            elif rectype == 0x04:  # extended linear address (<<16)
                ext_addr = ((data[0] << 8) | data[1]) << 16
            elif rectype in (0x03, 0x05):  # start address, não usado no .bin
                pass
            else:
                raise ValueError(f"tipo de registro Intel-Hex não suportado: {rectype:02X}")

    image = bytearray([fill]) * max_end
    for addr, data in chunks.items():
        image[addr:addr + len(data)] = data
    return bytes(image)


def main():
    if len(sys.argv) != 3:
        print(f"uso: {sys.argv[0]} entrada.h86 saida.bin", file=sys.stderr)
        sys.exit(1)
    image = hex_to_bin(sys.argv[1])
    with open(sys.argv[2], 'wb') as f:
        f.write(image)
    print(f"# {sys.argv[1]}: {len(image)} bytes ({len(image)/1024:.1f} KB) -> {sys.argv[2]}")


if __name__ == '__main__':
    main()
