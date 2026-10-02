#!/usr/bin/env python3
"""Regressão do BUG-12 da Sirius32 (01/10/2026): flags de ADDC/ADDCB/SUBC/
SUBCB no c166sim.py.

Manual (c166ism.pdf, ADDC/ADDCB/SUBC/SUBCB): "Z: Set if result equals zero
and the previous Z flag was set. Cleared otherwise." O simulador calculava Z
só com o resultado da instrução, então `SUB lo ; SUBC hi ; JMPR cc_Z/cc_NZ/
cc_ULE/cc_UGT` (comparação de 32 bits) dava "igual" sempre que as palavras
ALTAS eram iguais. De carona: V era calculado com o carry de entrada somado
ao op2 (`b + cin`), o que troca o sinal do "op2" quando ele é 0x7FFF/0x7F e
C=1 -> V invertido.

Confere:
  1. cada modo de endereçamento de ADDC/SUBC (word) e ADDCB/SUBCB (byte) -
     Rn,Rm / Rn,[Ri] / Rn,[Ri+] / Rn,#data3 / reg,#data / reg,mem / mem,reg -
     contra um modelo de referência independente: resultado, Z (encadeado),
     C, V e N, com C e Z de entrada nos dois estados e operandos de borda
     (0, 1, 0x7FFF, 0x8000, 0xFFFF, ...);
  2. comparação de 32 bits `SUB lo ; SUBC hi ; JMPR cc` com todas as
     condições que dependem de Z/C/N/V (Z, NZ, ULT, UGE, ULE, UGT, SLT, SGE,
     SLE, SGT), e a soma de 32 bits `ADD lo ; ADDC hi ; JMPR cc_Z/cc_NZ`;
  3. bytes REAIS do firmware Scenic 2.0 16v, file 0x1534 (`ADD r4,r10 ;
     ADDC r5,r11 ; SUB r4,r12 ; SUBC r5,r13 ; JMPR cc_NZ ; MOVB RL4,#1 ;
     RETS ; MOVB RL4,#0 ; RETS`), conferidos contra o .bin se ele estiver no
     lugar de sempre.

O flag E (op2 == 0x8000/0x80) não é rastreado pelo simulador (ver cc_true) e
por isso não é conferido aqui.

Uso: sim_addc_subc_z_test.py <diretório simulador/>
"""
import os
import random
import sys

SIM_DIR = sys.argv[1] if len(sys.argv) > 1 else 'simulador'
sys.path.insert(0, SIM_DIR)
from c166sim import Sim  # noqa: E402

falhas = []


def confere(nome, obtido, esperado):
    if obtido != esperado:
        falhas.append(f"{nome}: esperado={esperado!r} obtido={obtido!r}")


def w(v):
    return [v & 0xFF, (v >> 8) & 0xFF]


def executa(prog, regs=None, flags=None, mem16=None, mem8=None, max_passos=200):
    """Roda `prog` (bytes em org 0) até cair num RETS (DB 00). Devolve o Sim,
    ou None se o simulador recusou algum opcode."""
    s = Sim(bytes(prog) + bytes([0xDB, 0x00]))
    for n, v in (regs or {}).items():
        s.r[n] = v & 0xFFFF
    for a, v in (mem16 or {}).items():
        s.set_w16(a, v & 0xFFFF)
    for a, v in (mem8 or {}).items():
        s.mem[a] = v & 0xFF
    for f, v in (flags or {}).items():
        s.flags[f] = bool(v)
    try:
        for _ in range(max_passos):
            if s.mem[s.pc] == 0xDB and s.mem[s.pc + 1] == 0x00:
                return s
            s.step()
    except Exception as e:  # opcode não suportado etc.
        falhas.append(f"exceção rodando {bytes(prog).hex()}: {e}")
        return None
    falhas.append(f"programa não terminou: {bytes(prog).hex()}")
    return None


# --- modelo de referência (manual c166ism.pdf) -------------------------------
def referencia(soma, a, b, cin, zprev, largura):
    mask = (1 << largura) - 1
    sinal = 1 << (largura - 1)

    def comsinal(v):
        return v - (mask + 1) if v & sinal else v

    if soma:
        bruto = a + b + cin
        s = comsinal(a) + comsinal(b) + cin
        c = bruto > mask
    else:
        bruto = a - b - cin
        s = comsinal(a) - comsinal(b) - cin
        c = bruto < 0
    res = bruto & mask
    return res, {'Z': res == 0 and zprev, 'C': c,
                 'V': not (-sinal <= s < sinal), 'N': bool(res & sinal)}


# --- 1: cada modo de endereçamento -------------------------------------------
MEM = 0x2000     # RAM comum pro operando em memória
RA, RB, RI = 5, 6, 2     # destino, fonte, ponteiro ([Ri] só aceita R0-R3)
# byte: RL5 = nibble 0xA, RL6 = nibble 0xC (RLn = 2n, RHn = 2n+1)
BA, BB = 0xA, 0xC

# Cada modo: (nome, monta(opbase, b) -> bytes, onde fica op2, onde sai o
# resultado, faixa do op2). opbase = 0x10 (ADDC) ou 0x30 (SUBC); a variante
# de byte é opbase+1 nas formas x0/x2/x4/x6/x8 (-> x1/x3/x5/x7/x9).
MODOS_WORD = [
    ('Rw,Rw',      lambda o, b: [o + 0x0, (RA << 4) | RB],            'reg',  'reg', None),
    ('Rw,[Rw]',    lambda o, b: [o + 0x8, (RA << 4) | 0x8 | RI],      'ind',  'reg', None),
    ('Rw,[Rw+]',   lambda o, b: [o + 0x8, (RA << 4) | 0xC | RI],      'ind+', 'reg', None),
    ('Rw,#data3',  lambda o, b: [o + 0x8, (RA << 4) | (b & 7)],       'imm',  'reg', 7),
    ('reg,#data16', lambda o, b: [o + 0x6, 0xF0 | RA] + w(b),         'imm',  'reg', None),
    ('reg,mem',    lambda o, b: [o + 0x2, 0xF0 | RA] + w(MEM),        'mem',  'reg', None),
    ('mem,reg',    lambda o, b: [o + 0x4, 0xF0 | RB] + w(MEM),        'regb', 'mem', None),
]
MODOS_BYTE = [
    ('Rb,Rb',      lambda o, b: [o + 0x1, (BA << 4) | BB],            'reg',  'reg', None),
    ('Rb,[Rw]',    lambda o, b: [o + 0x9, (BA << 4) | 0x8 | RI],      'ind',  'reg', None),
    ('Rb,[Rw+]',   lambda o, b: [o + 0x9, (BA << 4) | 0xC | RI],      'ind+', 'reg', None),
    ('Rb,#data3',  lambda o, b: [o + 0x9, (BA << 4) | (b & 7)],       'imm',  'reg', 7),
    ('reg,#data8', lambda o, b: [o + 0x7, 0xF0 | BA, b & 0xFF, 0],    'imm',  'reg', None),
    ('reg,mem',    lambda o, b: [o + 0x3, 0xF0 | BA] + w(MEM),        'mem',  'reg', None),
    ('mem,reg',    lambda o, b: [o + 0x5, 0xF0 | BB] + w(MEM),        'regb', 'mem', None),
]

rng = random.Random(12)


def valores(largura, op2=False):
    # (cada execução cria um Sim novo, ~2 ms: a varredura fica nas bordas)
    topo = (1 << largura) - 1
    meio = 1 << (largura - 1)
    if op2:
        return [0, 1, meio - 1, meio, topo, rng.randrange(topo + 1)]
    return [0, 1, meio - 1, meio, meio + 1, topo - 1, topo]


def roda_modo(mnem, opbase, largura, modo, a, b, cin, zprev):
    nome, monta, onde_b, onde_res, lim = modo
    soma = opbase == 0x10
    lixo = 0xA5 if largura == 8 else 0     # byte alto dos registradores de byte
    regs = {RI: MEM}
    mem16, mem8 = {MEM: 0x5A5A, MEM + 2: 0x5A5A}, {}
    op1_na_mem = onde_res == 'mem'

    def poe_reg(n, v):
        regs[n] = (lixo << 8) | v if largura == 8 else v

    def poe_mem(v):
        if largura == 8:
            mem8[MEM] = v
        else:
            mem16[MEM] = v

    if op1_na_mem:
        poe_mem(a)
        poe_reg(RB, b)
    else:
        poe_reg(RA, a)
        if onde_b == 'reg':
            poe_reg(RB, b)
        elif onde_b in ('ind', 'ind+', 'mem'):
            poe_mem(b)
    s = executa(monta(opbase, b), regs, {'C': cin, 'Z': zprev, 'N': False, 'V': False},
                mem16, mem8)
    rot = f"{mnem} {nome} a={a:#x} b={b:#x} C={cin} Z={int(zprev)}"
    if s is None:
        falhas.append(rot + ": não executou")
        return
    res_esp, fl = referencia(soma, a, b, cin, zprev, largura)
    if op1_na_mem:
        obtido = s.mem[MEM] if largura == 8 else s.w16(MEM)
    else:
        obtido = s.r[RA] & 0xFF if largura == 8 else s.r[RA]
        if largura == 8:
            confere(rot + " byte alto preservado", s.r[RA] >> 8, lixo)
    confere(rot + " resultado", obtido, res_esp)
    for f in 'ZCVN':
        confere(rot + " flag " + f, bool(s.flags[f]), fl[f])
    if onde_b == 'ind+':
        confere(rot + " pós-incremento", s.r[RI], MEM + (1 if largura == 8 else 2))
    elif onde_b == 'ind':
        confere(rot + " ponteiro intacto", s.r[RI], MEM)


for largura, modos, sufixo in ((16, MODOS_WORD, ''), (8, MODOS_BYTE, 'B')):
    for mnem, opbase in (('ADDC' + sufixo, 0x10), ('SUBC' + sufixo, 0x30)):
        for modo in modos:
            for a in valores(largura):
                for b in ([0, 1, 2, 7] if modo[4] else valores(largura, True)):
                    for cin in (0, 1):
                        for zprev in (False, True):
                            roda_modo(mnem, opbase, largura, modo, a, b, cin, zprev)

# Casos nomeados (os do relato): Z encadeado e carry de entrada em C/V.
for nome, opbase, a, b, cin, zprev, res, z, c, v in (
        # palavras altas iguais, baixas diferentes: Z tem que continuar 0
        ('SUBC 5-5, Z ant.=0', 0x30, 5, 5, 0, False, 0, False, False, False),
        ('SUBC 5-5, Z ant.=1', 0x30, 5, 5, 0, True, 0, True, False, False),
        ('SUBC 5-4-1, Z ant.=1', 0x30, 5, 4, 1, True, 0, True, False, False),
        # op2 = 0xFFFF com carry=1: sempre empresta, nunca estoura com sinal
        ('SUBC 0-0xFFFF-1', 0x30, 0, 0xFFFF, 1, True, 0, True, True, False),
        ('SUBC 0x1234-0xFFFF-1', 0x30, 0x1234, 0xFFFF, 1, True, 0x1234, False, True, False),
        ('SUBC 0xFFFF-0xFFFF-1', 0x30, 0xFFFF, 0xFFFF, 1, True, 0xFFFF, False, True, False),
        # op2 = 0x7FFF com carry=1 (era aqui que V saía invertido)
        ('SUBC 0-0x7FFF-1', 0x30, 0, 0x7FFF, 1, True, 0x8000, False, True, False),
        ('SUBC 0x8000-0x7FFF-1', 0x30, 0x8000, 0x7FFF, 1, True, 0, True, False, True),
        ('SUBC 0xFFFF-0x7FFF-1', 0x30, 0xFFFF, 0x7FFF, 1, True, 0x7FFF, False, False, True),
        ('ADDC 0+0x7FFF+1', 0x10, 0, 0x7FFF, 1, True, 0x8000, False, False, True),
        ('ADDC 0x8000+0x7FFF+1', 0x10, 0x8000, 0x7FFF, 1, True, 0, True, True, False),
        ('ADDC 0+0xFFFF+1, Z ant.=1', 0x10, 0, 0xFFFF, 1, True, 0, True, True, False),
        ('ADDC 0+0xFFFF+1, Z ant.=0', 0x10, 0, 0xFFFF, 1, False, 0, False, True, False),
        ('ADDC 0+0+0, Z ant.=0', 0x10, 0, 0, 0, False, 0, False, False, False)):
    s = executa([opbase, (RA << 4) | RB], {RA: a, RB: b}, {'C': cin, 'Z': zprev})
    if s is None:
        continue
    confere(nome + " resultado", s.r[RA], res)
    confere(nome + " Z", bool(s.flags['Z']), z)
    confere(nome + " C", bool(s.flags['C']), c)
    confere(nome + " V", bool(s.flags['V']), v)

# --- 2: comparação/soma de 32 bits -------------------------------------------
# SUB r4,r12 ; SUBC r5,r13 ; JMPR cc,+2 ; MOV r1,#0 ; RETS ; MOV r1,#1 ; RETS
def s32(v):
    return v - (1 << 32) if v & 0x80000000 else v


CONDS = [
    (0x2, 'cc_Z',   lambda x, y: x == y),
    (0x3, 'cc_NZ',  lambda x, y: x != y),
    (0x8, 'cc_ULT', lambda x, y: x < y),
    (0x9, 'cc_UGE', lambda x, y: x >= y),
    (0xF, 'cc_ULE', lambda x, y: x <= y),
    (0xE, 'cc_UGT', lambda x, y: x > y),
    (0xC, 'cc_SLT', lambda x, y: s32(x) < s32(y)),
    (0xD, 'cc_SGE', lambda x, y: s32(x) >= s32(y)),
    (0xB, 'cc_SLE', lambda x, y: s32(x) <= s32(y)),
    (0xA, 'cc_SGT', lambda x, y: s32(x) > s32(y)),
]
PARES = [
    (0x00010000, 0x00010000), (0x00010000, 0x00010001), (0x00010001, 0x00010000),
    (0x00010005, 0x00010394), (0x0001FFFF, 0x00010000), (0x00020000, 0x00010000),
    (0x00010000, 0x00020000), (0x00000000, 0x00000000), (0x00000000, 0x00000001),
    (0x00000001, 0x00000000), (0x0000FFFF, 0x00010000), (0x00010000, 0x0000FFFF),
    (0xFFFFFFFF, 0xFFFFFFFF), (0xFFFFFFFF, 0x00000000), (0x00000000, 0xFFFFFFFF),
    (0x7FFFFFFF, 0x80000000), (0x80000000, 0x7FFFFFFF), (0x80000000, 0x80000000),
    (0x80000001, 0x80000000), (0x7FFF0000, 0x7FFF0001), (0x12345678, 0x12345678),
    (0x12345678, 0x12340000), (0x12340000, 0x12345678), (0xFFFF0000, 0xFFFF0001),
]
PARES += [(rng.randrange(1 << 32), rng.randrange(1 << 32)) for _ in range(6)]
# mesma palavra alta, baixa diferente (o caso que o simulador errava)
for _ in range(8):
    hi = rng.randrange(1 << 16) << 16
    PARES.append((hi | rng.randrange(1 << 16), hi | rng.randrange(1 << 16)))

for cc, nome, pred in CONDS:
    prog = [0x20, 0x4C, 0x30, 0x5D, (cc << 4) | 0xD, 0x02,
            0xE0, 0x01, 0xDB, 0x00, 0xE0, 0x11]
    for x, y in PARES:
        # Z e C de entrada "sujos": o SUB tem que apagá-los
        for sujo in (False, True):
            s = executa(prog, {4: x & 0xFFFF, 5: x >> 16, 12: y & 0xFFFF, 13: y >> 16, 1: 0xEEEE},
                        {'Z': sujo, 'C': sujo})
            if s is not None:
                confere(f"cmp32 {x:#010x} vs {y:#010x} {nome}", s.r[1], int(pred(x, y)))

# ADD r4,r12 ; ADDC r5,r13 ; JMPR cc_Z/cc_NZ : soma de 32 bits == 0 ?
for cc, nome, quer_zero in ((0x2, 'cc_Z', True), (0x3, 'cc_NZ', False)):
    prog = [0x00, 0x4C, 0x10, 0x5D, (cc << 4) | 0xD, 0x02,
            0xE0, 0x01, 0xDB, 0x00, 0xE0, 0x11]
    for x, y in ((0x00010000, 0xFFFF0000), (0x00010001, 0xFFFF0000), (0x00000001, 0xFFFFFFFF),
                 (0x00000005, 0xFFFFFFFF), (0x12340000, 0xEDCC0000), (0x12340001, 0xEDCC0000),
                 (0, 0), (0, 1), (0x80000000, 0x80000000), (0x80000001, 0x80000000)):
        s = executa(prog, {4: x & 0xFFFF, 5: x >> 16, 12: y & 0xFFFF, 13: y >> 16, 1: 0xEEEE})
        if s is not None:
            zero = ((x + y) & 0xFFFFFFFF) == 0
            confere(f"add32 {x:#010x} + {y:#010x} {nome}", s.r[1], int(zero == quer_zero))

# --- 3: bytes reais do firmware, file 0x1534 ---------------------------------
# MOV r4,0xFCBE ; MOV r5,#0 ; MOV r5,r4 ; MOV r4,0xFF1C ; MOV r10,0xFCC0 ;
# MOV r11,#0 ; ADD r4,r10 ; ADDC r5,r11 ; SUB r4,r12 ; SUBC r5,r13 ;
# JMPR cc_NZ,+2 ; MOVB RL4,#1 ; RETS ; MOVB RL4,#0 ; RETS
# 0xFF1C é o SFR ZEROS (sempre 0), então r5:r4 = [0xFCBE]:0 + 0:[0xFCC0], ou
# seja: RL4 = ([0xFCBE]:[0xFCC0] == r13:r12)
FW_1534 = bytes.fromhex('f2f4befc e005 f054 f2f41cff f2fac0fc e00b '
                        '004a 105b 204c 305d 3d02 e118 db00 e108 db00'.replace(' ', ''))
# só o miolo (a partir do ADD), com os operandos já nos registradores
FW_1534_MIOLO = FW_1534[0x12:]

bin_fw = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Scenic 2.0 16v.bin')
if os.path.exists(bin_fw):
    fw = open(bin_fw, 'rb').read()
    confere("bytes do .bin em 0x01534", fw[0x1534:0x1534 + len(FW_1534)].hex(), FW_1534.hex())

CASOS_1534 = [
    # (r5:r4 antes da soma, r10, r13:r12)
    (0x00010000, 0x0005, 0x00010005),   # igual
    (0x00010000, 0x0005, 0x00010006),   # alta igual, baixa diferente (BUG-12)
    (0x00010000, 0x0005, 0x00010000),   # alta igual, baixa diferente (BUG-12)
    (0x00010000, 0x0005, 0x00020005),   # baixa igual, alta diferente
    (0x0001FFFF, 0x0001, 0x00020000),   # vai-um da soma, igual
    (0x0001FFFF, 0x0001, 0x00010000),   # vai-um da soma, baixa igual, alta diferente
    (0x0001FFFF, 0x0002, 0x00020000),   # vai-um, alta igual, baixa diferente (BUG-12)
    (0xFFFFFFFF, 0x0001, 0x00000000),   # dá a volta, igual
    (0xFFFFFFFF, 0x0002, 0x00000000),   # dá a volta, baixa diferente (BUG-12)
    (0x00000000, 0x0000, 0x00000000),
    (0x00000000, 0x0000, 0x00000001),
    (0x80000000, 0x0000, 0x80000000),
    (0x80000000, 0x0000, 0x00000000),
]
for _ in range(20):
    base, inc = rng.randrange(1 << 32), rng.randrange(1 << 16)
    soma = (base + inc) & 0xFFFFFFFF
    alvo = rng.choice([soma, soma ^ rng.randrange(1, 1 << 16), soma ^ (rng.randrange(1, 1 << 16) << 16)])
    CASOS_1534.append((base, inc, alvo))
    # e os mesmos três tipos de alvo com a palavra baixa da base zerada (é o
    # que a rotina inteira enxerga)
    base &= 0xFFFF0000
    CASOS_1534.append((base, inc, rng.choice([base | inc, base | (inc ^ rng.randrange(1, 1 << 16)),
                                              (base ^ (rng.randrange(1, 1 << 16) << 16)) | inc])))

for base, inc, alvo in CASOS_1534:
    esp = int(((base + inc) & 0xFFFFFFFF) == alvo)
    rot = f"fw 0x1534 ({base:#010x} + {inc:#06x} == {alvo:#010x})"
    s = executa(list(FW_1534_MIOLO),
                {4: base & 0xFFFF, 5: base >> 16, 10: inc, 11: 0, 12: alvo & 0xFFFF, 13: alvo >> 16})
    if s is not None:
        confere(rot + " miolo", s.r[4] & 0xFF, esp)
    # rotina inteira, operandos na RAM que ela lê (palavra baixa = só 0xFCC0)
    esp = int((base & 0xFFFF0000 | inc) == alvo)
    s = executa(list(FW_1534), {12: alvo & 0xFFFF, 13: alvo >> 16},
                mem16={0xFCBE: base >> 16, 0xFCC0: inc})
    if s is not None:
        confere(f"fw 0x1534 rotina inteira ({base >> 16:#06x}:{inc:#06x} == {alvo:#010x})",
                s.r[4] & 0xFF, esp)

if falhas:
    print('\n'.join(falhas[:50]))
    print(f"... {len(falhas)} falha(s)")
    sys.exit(1)
print("OK: Z encadeado e C/V/N de ADDC/ADDCB/SUBC/SUBCB em todos os modos de endereçamento")
