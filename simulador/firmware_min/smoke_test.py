#!/usr/bin/env python3
"""Teste de fumaça standalone do firmware_min (ver boot.asm) - injeta o
quadro K-line de pedido "0100" (Mode 01 PID 00) byte a byte via
`Sim.uart_inject_rx_byte()` e confere que a resposta framed correta sai em
`Sim.uart_pop_tx_bytes()`. Não usa o obd2_bridge/ELM327 - só valida que o
.bin roda certo isoladamente antes de subir a ponte de verdade.

Uso: python3 smoke_test.py [boot.bin]
"""
import sys

sys.path.insert(0, '.')
import c166sim

STEPS_PER_BYTE = 2_000
STEPS_IDLE = 50_000

REQUEST_FRAME = bytes([0x82, 0x7A, 0xF1, 0x01, 0x00, 0xEE])
EXPECTED_RESPONSE = bytes([0x86, 0xF1, 0x7A, 0x41, 0x00, 0xBE, 0x3E, 0x80, 0x10, 0xBE])


def main():
    bin_path = sys.argv[1] if len(sys.argv) > 1 else 'boot.bin'
    with open(bin_path, 'rb') as f:
        image = f.read()
    sim = c166sim.Sim(image)

    # roda um pouco antes de qualquer coisa, só pra confirmar que o
    # superloop não trava/trapeia sozinho (não deve ter nenhum trap: é só
    # MOV/CMP/JMP em loop esperando byte)
    try:
        for _ in range(STEPS_IDLE):
            sim.step()
    except c166sim.Trap as e:
        print(f"FALHA: trap durante idle antes de qualquer requisição: {e} (pc=0x{sim.pc:06X})")
        sys.exit(1)
    print(f"OK: {STEPS_IDLE} passos em idle sem trap (pc=0x{sim.pc:06X})")

    for b in REQUEST_FRAME:
        sim.uart_inject_rx_byte(b)
        try:
            for _ in range(STEPS_PER_BYTE):
                sim.step()
        except c166sim.Trap as e:
            print(f"FALHA: trap ao processar byte 0x{b:02X}: {e} (pc=0x{sim.pc:06X})")
            sys.exit(1)

    try:
        for _ in range(STEPS_IDLE):
            sim.step()
    except c166sim.Trap as e:
        print(f"FALHA: trap esperando resposta: {e} (pc=0x{sim.pc:06X})")
        sys.exit(1)

    got = bytes(sim.uart_pop_tx_bytes())
    print(f"resposta recebida: {got.hex()}")
    print(f"resposta esperada: {EXPECTED_RESPONSE.hex()}")
    if got == EXPECTED_RESPONSE:
        print("OK: resposta bate exatamente com o esperado")
        sys.exit(0)
    else:
        print("FALHA: resposta não bate")
        sys.exit(1)


if __name__ == '__main__':
    main()
