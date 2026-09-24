#!/usr/bin/env python3
"""Regressão do BUG-3 da Sirius32 (24/09/2026, ver docs/limitations.md):
codificação de NEG/CPL Rwn ("81 n0"/"91 n0") e NEGB/CPLB Rbn ("A1 n0"/
"B1 n0") - registrador no NIBBLE ALTO do 2º byte, nibble baixo 0 (manual
Infineon, c166ism.pdf). Antes o c166sim.py tratava o 2º byte como campo
'reg' compacto (>=0xF0 = GPR pelo nibble baixo, senão SFR 0xFE00+2*b) e o
c166asm.py emitia a forma própria "81 Fn"/"91 Fn".

Confere:
  1. bytes emitidos pelo c166asm.py pra NEG/CPL em R0, R4, R13 e R15;
  2. execução no c166sim.py (resultado + flags Z/N/C/V) desses programas;
  3. NEGB/CPLB (bytes montados à mão - o c166asm.py não tem essas
     mnemônicas, o c167cc não as emite) em RL0, RH0, RL7, RH7;
  4. os bytes REAIS do firmware Scenic 2.0 16v: "91 D0" (file 0x27D1E,
     CPL R13) e "81 40 18 50 81 50" (file 0x272E4, NEG R4 ; ADDC R5,#0 ;
     NEG R5 = negação de 32 bits R5:R4);
  5. nibble baixo != 0 vira Trap explícito (não é codificação válida).

Uso: sim_cpl_neg_test.py <diretório simulador/>
"""
import sys

sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else 'simulador')
from c166asm import Asm  # noqa: E402
from c166sim import Sim, Trap  # noqa: E402

falhas = []


def confere(nome, obtido, esperado):
    if obtido != esperado:
        falhas.append(f"{nome}: esperado={esperado!r} obtido={obtido!r}")


def montar_texto(src):
    import os
    import tempfile
    with tempfile.NamedTemporaryFile('w', suffix='.asm', delete=False) as f:
        f.write(src)
        caminho = f.name
    try:
        a = Asm()
        a.load_file(caminho)
        img, *_ = a.assemble(org=0)
    finally:
        os.unlink(caminho)
    return bytes(img)


def flags(sim):
    return {k: bool(sim.flags[k]) for k in ('Z', 'N', 'C', 'V')}


def roda(img, passos, regs=None):
    s = Sim(img)
    for n, v in (regs or {}).items():
        s.r[n] = v
    for _ in range(passos):
        s.step()
    return s


# --- 1+2: NEG/CPL montados pelo c166asm.py ---------------------------------
for n in (0, 4, 13, 15):
    for mn, opc in (('NEG', 0x81), ('CPL', 0x91)):
        img = montar_texto(f"        {mn} R{n}\n")
        confere(f"{mn} R{n} bytes", img[:2].hex(), bytes([opc, n << 4]).hex())
        for val in (0x0000, 0x0001, 0x1234, 0x8000, 0xFFFF):
            # R(n^1) como sentinela: não pode ser tocado
            outro = n ^ 1
            s = roda(img, 1, {n: val, outro: 0xA5A5})
            if mn == 'NEG':
                esp = (-val) & 0xFFFF
                fl = {'Z': esp == 0, 'N': bool(esp & 0x8000),
                      'C': val != 0, 'V': val == 0x8000}
            else:
                esp = (~val) & 0xFFFF
                fl = {'Z': esp == 0, 'N': bool(esp & 0x8000),
                      'C': False, 'V': False}
            confere(f"{mn} R{n} ({val:#06x}) resultado", s.r[n], esp)
            confere(f"{mn} R{n} ({val:#06x}) flags", flags(s), fl)
            confere(f"{mn} R{n} ({val:#06x}) R{outro} intacto", s.r[outro], 0xA5A5)
            confere(f"{mn} R{n} pc", s.pc, 2)

# --- 3: NEGB/CPLB (bytes à mão) ---------------------------------------------
# Rb nibble = (regnum<<1)|sel (sel 0=RL, 1=RH), mesma convenção de get_breg.
for nome, nib in (('RL0', 0x0), ('RH0', 0x1), ('RL7', 0xE), ('RH7', 0xF)):
    regnum, alto = nib >> 1, nib & 1
    for mn, opc in (('NEGB', 0xA1), ('CPLB', 0xB1)):
        for val in (0x00, 0x01, 0x7F, 0x80, 0xFF):
            outro_byte = 0x5A
            w = ((val << 8) | outro_byte) if alto else ((outro_byte << 8) | val)
            s = roda(bytes([opc, nib << 4]), 1, {regnum: w})
            if mn == 'NEGB':
                esp = (-val) & 0xFF
                fl = {'Z': esp == 0, 'N': bool(esp & 0x80),
                      'C': val != 0, 'V': val == 0x80}
            else:
                esp = (~val) & 0xFF
                fl = {'Z': esp == 0, 'N': bool(esp & 0x80),
                      'C': False, 'V': False}
            esp_w = ((esp << 8) | outro_byte) if alto else ((outro_byte << 8) | esp)
            confere(f"{mn} {nome} ({val:#04x}) palavra", s.r[regnum], esp_w)
            confere(f"{mn} {nome} ({val:#04x}) flags", flags(s), fl)

# --- 4: bytes reais do firmware ---------------------------------------------
# file 0x27D1E: "91 D0" = CPL R13 (antes: CPL no SFR 0xFFA0, R13 intacto)
s = roda(bytes([0x91, 0xD0]), 1, {13: 0x0004})
confere("real 91 D0: R13", s.r[13], 0xFFFB)
# file 0x2EF3E: "81 F0" = NEG R15 (antes: o simulador lia como NEG R0)
s = roda(bytes([0x81, 0xF0]), 1, {15: 0x0003, 0: 0x1111})
confere("real 81 F0: R15", s.r[15], 0xFFFD)
confere("real 81 F0: R0 intacto", s.r[0], 0x1111)
# file 0x272E4: NEG R4 ; ADDC R5,#0 ; NEG R5 -> R5:R4 = -(R5:R4)
real = bytes([0x81, 0x40, 0x18, 0x50, 0x81, 0x50])
for v32 in (0x00000000, 0x00000001, 0x00010000, 0x12345678, 0x80000000, 0xFFFFFFFF):
    s = roda(real, 3, {4: v32 & 0xFFFF, 5: v32 >> 16})
    esp = (-v32) & 0xFFFFFFFF
    confere(f"real NEG32 ({v32:#010x})", (s.r[5] << 16) | s.r[4], esp)

# --- 5: nibble baixo != 0 é inválido ----------------------------------------
for opc in (0x81, 0x91, 0xA1, 0xB1):
    try:
        roda(bytes([opc, 0xF3]), 1)
        falhas.append(f"{opc:02X} F3 deveria dar Trap (nibble baixo != 0)")
    except Trap:
        pass

if falhas:
    print('\n'.join(falhas))
    sys.exit(1)
print("OK: NEG/CPL/NEGB/CPLB na codificação real 'n0'")
