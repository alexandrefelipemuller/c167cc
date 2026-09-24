#!/usr/bin/env python3
"""Regressão do BUG-5 da Sirius32 (24/09/2026, ver docs/limitations.md):
endereços de MDL e MDH estavam TROCADOS no c166sim.py/c166asm.py
(MDL=0xFE0C, MDH=0xFE0E). O manual do C167CR (c167cr_userguide.pdf, tabela
de SFR) dá MDH = FE0Ch (reg 06h) e MDL = FE0Eh (reg 07h).

O código gerado pelo c167cc não sentia a troca (montador e simulador usam o
NOME), mas o firmware original acessa MD por ENDEREÇO. Por isso este teste
nunca lê por nome: tudo é lido/escrito por endereço (forma "mem" F2/F6 com
0xFE0C/0xFE0E) ou pelo campo 'reg' compacto (06h/07h).

Confere:
  1. constantes MDL_ADDR/MDH_ADDR do c166sim.py e do c166asm.py;
  2. c166asm.py: "MOV Rn, MDL"/"MOV MDH, Rn" montam com 0xFE0E/0xFE0C;
  3. execução: após MULU/MUL/DIVU/DIV/DIVLU/DIVL, o valor esperado está no
     ENDEREÇO certo (0xFE0E = MDL = produto baixo/quociente; 0xFE0C = MDH =
     produto alto/resto), inclusive pelo campo reg 06h/07h;
  4. bytes REAIS do firmware Scenic 2.0 16v que acessam MD por endereço:
     file 0x1682 (MULU saturado em 0x7FFF), file 0x16A2 (DIVL de r13:r12
     saturado) e file 0x17AC (resto de x/3 lido de 0xFE0C). Conferidos contra
     o .bin se ele estiver no lugar de sempre.

Uso: sim_md_addr_test.py <diretório simulador/>
"""
import os
import sys

SIM_DIR = sys.argv[1] if len(sys.argv) > 1 else 'simulador'
sys.path.insert(0, SIM_DIR)
import c166asm  # noqa: E402
import c166sim  # noqa: E402
from c166sim import Sim  # noqa: E402

falhas = []


def confere(nome, obtido, esperado):
    if obtido != esperado:
        falhas.append(f"{nome}: esperado={esperado!r} obtido={obtido!r}")


def w(v):
    return [v & 0xFF, (v >> 8) & 0xFF]


def mov_ri(n, imm):          # MOV Rn, #imm16          (E6 Fn ii ii)
    return [0xE6, 0xF0 | n] + w(imm)


def mov_r_mem(n, addr):      # MOV Rn, mem             (F2 Fn aa aa)
    return [0xF2, 0xF0 | n] + w(addr)


def mov_mem_r(addr, n):      # MOV mem, Rn             (F6 Fn aa aa)
    return [0xF6, 0xF0 | n] + w(addr)


def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def executa(prog, regs=None, max_passos=200):
    """Roda `prog` (lista de bytes em org 0) até cair num RETS (DB 00) ou
    acabar o código. MD começa com lixo conhecido pra nada passar por sorte."""
    img = bytes(prog) + bytes([0xDB, 0x00])
    s = Sim(img)
    s.set_w16(0xFE0C, 0xDEAD)
    s.set_w16(0xFE0E, 0xBEEF)
    s.set_special(0xFE0C, 0xDEAD)
    s.set_special(0xFE0E, 0xBEEF)
    for n, v in (regs or {}).items():
        s.r[n] = v & 0xFFFF
    for _ in range(max_passos):
        if s.mem[s.pc] == 0xDB and s.mem[s.pc + 1] == 0x00:
            return s
        s.step()
    falhas.append("programa não terminou")
    return s


# --- 1: constantes ----------------------------------------------------------
for mod in (c166sim, c166asm):
    confere(f"{mod.__name__}.MDL_ADDR", mod.MDL_ADDR, 0xFE0E)
    confere(f"{mod.__name__}.MDH_ADDR", mod.MDH_ADDR, 0xFE0C)

# --- 2: montador ------------------------------------------------------------
import tempfile  # noqa: E402


def montar_texto(src):
    with tempfile.NamedTemporaryFile('w', suffix='.asm', delete=False) as f:
        f.write(src)
        caminho = f.name
    try:
        a = c166asm.Asm()
        a.load_file(caminho)
        img, *_ = a.assemble(org=0)
    finally:
        os.unlink(caminho)
    return bytes(img)


img = montar_texto("MOV R4, MDL\nMOV R5, MDH\nMOV MDL, R1\nMOV MDH, R2\n")
confere("asm MOV R4, MDL", img[0:4].hex(), 'f2f40efe')
confere("asm MOV R5, MDH", img[4:8].hex(), 'f2f50cfe')
confere("asm MOV MDL, R1", img[8:12].hex(), 'f6f10efe')
confere("asm MOV MDH, R2", img[12:16].hex(), 'f6f20cfe')

# --- 3: execução, lendo MD por endereço ------------------------------------
# lê o par de volta por endereço: R4 <- [0xFE0E] (MDL), R5 <- [0xFE0C] (MDH)
LE_MD = mov_r_mem(4, 0xFE0E) + mov_r_mem(5, 0xFE0C)

# MULU R1,R2 (1B 12): 0x1234 * 0x5678 = 0x0626_0060
s = executa(mov_ri(1, 0x1234) + mov_ri(2, 0x5678) + [0x1B, 0x12] + LE_MD)
confere("MULU: [0xFE0E] = produto baixo", s.r[4], 0x0060)
confere("MULU: [0xFE0C] = produto alto", s.r[5], 0x0626)
# MUL R1,R2 (0B 12) com sinal: -3 * 1000 = -3000 = 0xFFFF_F448
s = executa(mov_ri(1, -3 & 0xFFFF) + mov_ri(2, 1000) + [0x0B, 0x12] + LE_MD)
confere("MUL: [0xFE0E] = produto baixo", s.r[4], 0xF448)
confere("MUL: [0xFE0C] = produto alto", s.r[5], 0xFFFF)

# DIVU R2 (5B 22): dividendo escrito por endereço em 0xFE0E (MDL)
s = executa(mov_ri(1, 1000) + mov_ri(2, 7) + mov_mem_r(0xFE0E, 1) + [0x5B, 0x22] + LE_MD)
confere("DIVU 1000/7: [0xFE0E] = quociente", s.r[4], 142)
confere("DIVU 1000/7: [0xFE0C] = resto", s.r[5], 6)
# DIV R2 (4B 22) com sinal: -1000/7 = -142 resto -6
s = executa(mov_ri(1, -1000 & 0xFFFF) + mov_ri(2, 7) + mov_mem_r(0xFE0E, 1) + [0x4B, 0x22] + LE_MD)
confere("DIV -1000/7: [0xFE0E] = quociente", s16(s.r[4]), -142)
confere("DIV -1000/7: [0xFE0C] = resto", s16(s.r[5]), -6)
# DIVLU R2 (7B 22): 1000000 = 0x000F_4240 -> MDH(0xFE0C)=0x000F, MDL(0xFE0E)=0x4240
s = executa(mov_ri(1, 0x000F) + mov_ri(3, 0x4240) + mov_ri(2, 300)
            + mov_mem_r(0xFE0C, 1) + mov_mem_r(0xFE0E, 3) + [0x7B, 0x22] + LE_MD)
confere("DIVLU 1000000/300: [0xFE0E] = quociente", s.r[4], 3333)
confere("DIVLU 1000000/300: [0xFE0C] = resto", s.r[5], 100)
# DIVL R2 (6B 22): -1000000 / 300 = -3333 resto -100
m = -1000000 & 0xFFFFFFFF
s = executa(mov_ri(1, m >> 16) + mov_ri(3, m & 0xFFFF) + mov_ri(2, 300)
            + mov_mem_r(0xFE0C, 1) + mov_mem_r(0xFE0E, 3) + [0x6B, 0x22] + LE_MD)
confere("DIVL -1000000/300: [0xFE0E] = quociente", s16(s.r[4]), -3333)
confere("DIVL -1000000/300: [0xFE0C] = resto", s16(s.r[5]), -100)

# Campo 'reg' compacto: MOV reg,#imm (E6 06/E6 07) carrega MDH/MDL e
# MOV mem,reg (F6 06/F6 07) salva de volta em RAM comum.
s = executa([0xE6, 0x06] + w(0x000F) + [0xE6, 0x07] + w(0x4240) + mov_ri(2, 300)
            + [0x7B, 0x22] + [0xF6, 0x07] + w(0x2000) + [0xF6, 0x06] + w(0x2002))
confere("reg 07h = MDL (quociente)", s.w16(0x2000), 3333)
confere("reg 06h = MDH (resto)", s.w16(0x2002), 100)

# --- 4: bytes reais do firmware ---------------------------------------------
# file 0x1682: MOV r4,r13; MULU r4,r12; MOV r5,0xFE0E; CMP r5,#0x7FFF;
# JMPR ugt; MOV r5,0xFE0C; JMPR nz; MOV r4,0xFE0E; RETS; MOV r4,#0x7FFF; RETS
# -> r4 = r12*r13 saturado em 0x7FFF (só faz sentido com MDL=0xFE0E).
FW_MULU_SAT = bytes.fromhex('f04d 1b4c f2f50efe 46f5ff7f '
                            'ed06 f2f50cfe 3d03 f2f40efe db00 e6f4ff7f db00'.replace(' ', ''))
# file 0x16A2: CMP r14,#0; JMPR z; MOV 0xFE0C,r13; MOV 0xFE0E,r12; DIVL r14;
# JMPR v; MOV r4,0xFE0E; RETS   (só o caminho sem overflow/divisor 0)
FW_DIVL = bytes.fromhex('48e0 2d18 f6fd0cfe f6fc0efe 6bee 4d03 f2f40efe db00'.replace(' ', ''))
# file 0x17AC: MOV r5,#3 (E0 35, #data4); MOV 0xFE0E,r4; DIVU r5; MOV r4,0xFE0C (= x % 3)
FW_MOD3 = bytes.fromhex('e035 f6f40efe 5b55 f2f40cfe'.replace(' ', ''))

bin_fw = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Scenic 2.0 16v.bin')
if os.path.exists(bin_fw):
    fw = open(bin_fw, 'rb').read()
    for off, bs in ((0x1682, FW_MULU_SAT), (0x16A2, FW_DIVL), (0x17AC, FW_MOD3)):
        confere(f"bytes do .bin em {off:#07x}", fw[off:off + len(bs)].hex(), bs.hex())

for r12, r13, esp in ((100, 200, 20000), (300, 200, 0x7FFF), (0x100, 0x100, 0x7FFF),
                      (181, 181, 32761), (0, 0xFFFF, 0)):
    s = executa(list(FW_MULU_SAT), {12: r12, 13: r13})
    confere(f"fw 0x1682 MULU sat {r12}*{r13}", s.r[4], esp)

for dividendo, divisor, esp in ((1000000, 300, 3333), (-70000, 1000, -70), (65536 * 3 + 7, 4, 49153 & 0xFFFF)):
    m = dividendo & 0xFFFFFFFF
    s = executa(list(FW_DIVL), {12: m & 0xFFFF, 13: m >> 16, 14: divisor})
    if dividendo == 65536 * 3 + 7:
        # 196615/4 = 49153 > 0x7FFF -> V=1, cai no caminho de saturação
        # (fora do trecho copiado): só confere que não passou pelo RETS normal
        confere("fw 0x16A2 DIVL overflow -> V", bool(s.flags['V']), True)
        continue
    confere(f"fw 0x16A2 DIVL {dividendo}/{divisor}", s16(s.r[4]), esp)

for x in (0, 1, 2, 3, 100, 65535):
    s = executa(list(FW_MOD3), {4: x})
    confere(f"fw 0x17AC {x} % 3", s.r[4], x % 3)

if falhas:
    print('\n'.join(falhas[:50]))
    print(f"... {len(falhas)} falha(s)")
    sys.exit(1)
print("OK: MDH=0xFE0C, MDL=0xFE0E no c166sim/c166asm")
