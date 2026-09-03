#!/usr/bin/env python3
"""Teste de regressão do módulo CRC16 compilado (src/10_crc16.asm), embutido
em firmware_full.bin - genuinamente COMPILADO de
reimplementacao_c/checksum/crc16_sirius32.c pelo c167cc (ver README.md deste
diretório, seção do módulo CRC16).

Chama crc16_sirius32(buf,len,init) diretamente pelo endereço (não existe
call site real via K-line ainda - ver comentário em src/10_crc16.asm sobre
por que este módulo fica sem trampolim/dispatcher), injetando registradores
R4/R5/R6 (ABI real do c167cc) e comparando contra ../../crc_sirius32.py
(fonte da verdade) rodado sobre os MESMOS bytes de um dump real
("Scenic 2.0 16v.bin").

Limitação HONESTA deste teste (não é bug do compilador nem do firmware):
o buffer de teste e a pilha usada aqui são mantidos dentro da janela lógica
0x0000-0x3FFF (DPP0, identidade garantida por reset - ver c166sim.py
translate_mem()) porque colocar um dos dois fora dessa janela exige
programar DPP1-3 primeiro (o firmware real faria isso no boot, mas
00_header.asm deste experimento nunca precisou até agora, já que nada aqui
ultrapassava 0x4000). Isso limita o teste a ~8000 bytes por vez em vez do
range real de ~32KB do descritor de código - já é evidência forte o
suficiente (2 tamanhos diferentes, ambos batendo byte-a-byte com a
referência Python sobre dados reais do dump).

Uso: python3 crc16_module_test.py [firmware_full.bin]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
import c166sim
from crc_sirius32 import crc16

def run_one(sim_img, buf, init):
    sim = c166sim.Sim(bytearray(sim_img) + b'\x00' * 0x20000)
    stage_addr = 0x2000
    sim.mem[stage_addr:stage_addr + len(buf)] = buf

    sim.r[4] = stage_addr
    sim.r[5] = len(buf) & 0xFFFF
    sim.r[6] = init
    sim.set_special(c166sim.SP_ADDR, 0x3FF0)
    sentinel = 0x3FEE
    sp = sim.get_special(c166sim.SP_ADDR) - 2
    sim.set_w16(sp, sentinel)
    sim.set_special(c166sim.SP_ADDR, sp)
    sim.pc = CRC16_ADDR

    steps = 0
    while sim.pc != sentinel and steps < 3_000_000:
        sim.step()
        steps += 1
    if steps >= 3_000_000:
        raise RuntimeError("nao convergiu (possivel loop infinito)")
    return sim.r[0]


def main():
    global CRC16_ADDR
    bin_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "firmware_full.bin")
    asm_path = os.path.join(HERE, "firmware_full.asm")

    # endereço do label vem da saída do próprio montador (não há símbolos no
    # .bin) - reassembla pra /dev/null só pra capturar o mapa de labels.
    import subprocess
    result = subprocess.run(
        [sys.executable, os.path.join(HERE, '..', 'c166asm.py'), asm_path, '/dev/null'],
        capture_output=True, text=True, check=True,
    )
    for tok in result.stdout.replace(',', ' ').split():
        if tok.startswith("crc16_sirius32=0x"):
            CRC16_ADDR = int(tok.split('=0x')[1], 16)
            break
    if CRC16_ADDR is None:
        raise SystemExit("nao foi possivel resolver o endereco de crc16_sirius32")

    dump_path = os.path.join(HERE, '..', '..', 'Scenic 2.0 16v.bin')
    with open(dump_path, 'rb') as f:
        dump = f.read()
    with open(bin_path, 'rb') as f:
        sim_img = f.read()

    init = 0x3341  # ver crc_sirius32.py - "A3" lido em file 0x805A deste dump
    base = 0x805E  # inicio da faixa de calibracao (ver descritores() no .py)

    failures = 0
    for n in (100, 3000, 8000):
        buf = dump[base:base + n]
        expected = crc16(buf, init)
        got = run_one(sim_img, buf, init)
        ok = got == expected
        status = "OK" if ok else "FALHA"
        print(f"[{status}] crc16_sirius32(n={n}): got={got:04X} want={expected:04X}")
        if not ok:
            failures += 1

    print()
    if failures:
        print(f"FALHA: {failures} verificacao(oes) nao bateram")
        sys.exit(1)
    print("OK: todas as verificacoes bateram (crc16_sirius32 compilado == crc_sirius32.py sobre dados reais)")
    sys.exit(0)


if __name__ == "__main__":
    main()
