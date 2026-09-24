#!/usr/bin/env python3
"""Regressão do BUG-4 da Sirius32 (24/09/2026, ver docs/limitations.md):
codificação de DIV/DIVU/DIVL/DIVLU Rwn ("4B nn"/"5B nn"/"6B nn"/"7B nn") -
registrador divisor REPETIDO nos dois nibbles do 2º byte (manual Infineon,
c166ism.pdf p.72-75). Antes o c166sim.py tratava o 2º byte como campo
'reg' compacto (>=0xF0 = GPR pelo nibble baixo, senão SFR 0xFE00+2*b) e o
c166asm.py emitia a forma própria "xB Fn".

Confere:
  1. bytes emitidos pelo c166asm.py pra DIV/DIVU/DIVL/DIVLU em R0, R5, R15;
  2. execução no c166sim.py: quociente em MDL, resto em MDH, casos com
     sinal (truncamento pra zero, resto com o sinal do dividendo), overflow
     de quociente (V=1) e divisor 0 (V=1, C=0, MD intacto), flags Z/N/C/V;
  3. os bytes REAIS do firmware Scenic 2.0 16v: "5B 55" (file 0x3ADB6,
     DIVU R5), "4B 22" (0x3ADE0, DIV R2) e "6B EE" (0x16AE, DIVL R14) -
     conferidos contra o .bin se ele estiver no lugar de sempre, e contra
     o ferramentas_disassembly/c166dis.py se ele existir;
  4. nibbles diferentes viram Trap explícito (não é codificação válida).

Uso: sim_div_test.py <diretório simulador/>
"""
import os
import sys

SIM_DIR = sys.argv[1] if len(sys.argv) > 1 else 'simulador'
sys.path.insert(0, SIM_DIR)
from c166asm import Asm  # noqa: E402
from c166sim import Sim, Trap, MDL_ADDR, MDH_ADDR  # noqa: E402

falhas = []


def confere(nome, obtido, esperado):
    if obtido != esperado:
        falhas.append(f"{nome}: esperado={esperado!r} obtido={obtido!r}")


def montar_texto(src):
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


def roda(img, n, divisor, mdl, mdh=0, sentinela=True):
    """Executa 1 instrução com R<n>=divisor, MD=mdh:mdl. Os outros GPRs
    recebem um padrão sentinela (0xA500|i) que não pode ser usado como
    divisor nem alterado."""
    s = Sim(img)
    if sentinela:
        for i in range(16):
            s.r[i] = 0xA500 | i
    s.r[n] = divisor & 0xFFFF
    s.set_special(MDL_ADDR, mdl & 0xFFFF)
    s.set_special(MDH_ADDR, mdh & 0xFFFF)
    s.flags['C'] = True   # tem que sair zerado
    s.step()
    for i in range(16):
        if i != n and sentinela:
            confere(f"R{i} intacto", s.r[i], 0xA500 | i)
    confere("pc", s.pc, 2)
    return s


def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def s32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def div_trunc(a, b):
    q = abs(a) // abs(b)
    q = -q if (a < 0) != (b < 0) else q
    return q, a - q * b


OPC = {'DIV': 0x4B, 'DIVU': 0x5B, 'DIVL': 0x6B, 'DIVLU': 0x7B}

# (dividendo de 32 bits - só MDL conta em DIV/DIVU -, divisor de 16 bits)
CASOS = [
    (100, 7), (50000, 7), (0xFFFF, 1), (0, 5), (-100 & 0xFFFF, 7),
    (100, -7 & 0xFFFF), (-100 & 0xFFFF, -7 & 0xFFFF), (0x8000, 0xFFFF),
    (1000000, 300), (0x00123456, 0x1234), (-1000000 & 0xFFFFFFFF, 300),
    (0x7FFFFFFF, 2), (0xFFFFFFFF, 0xFFFF), (12345, 0),
]

# --- 1+2: montados pelo c166asm.py e executados -----------------------------
for n in (0, 5, 15):
    for mn, opc in OPC.items():
        img = montar_texto(f"        {mn} R{n}\n")
        confere(f"{mn} R{n} bytes", img[:2].hex(), bytes([opc, (n << 4) | n]).hex())
        for md, dv in CASOS:
            mdl, mdh = md & 0xFFFF, (md >> 16) & 0xFFFF
            nome = f"{mn} R{n} MD={md:#010x} / {dv:#06x}"
            s = roda(img, n, dv, mdl, mdh)
            if mn in ('DIV', 'DIVU'):
                a = s16(mdl) if mn == 'DIV' else mdl
            else:
                a = s32(md) if mn == 'DIVL' else md
            b = s16(dv) if mn in ('DIV', 'DIVL') else dv
            if b == 0:
                # divisor 0: V=1, C=0, MD intacto (valor indefinido no manual)
                confere(f"{nome} V", s.flags['V'], True)
                confere(f"{nome} C", s.flags['C'], False)
                confere(f"{nome} MDL", s.get_special(MDL_ADDR), mdl)
                confere(f"{nome} MDH", s.get_special(MDH_ADDR), mdh)
                continue
            q, r = div_trunc(a, b)
            if mn in ('DIV', 'DIVL'):
                v = not (-0x8000 <= q <= 0x7FFF)
            else:
                v = q > 0xFFFF
            confere(f"{nome} MDL (quociente)", s.get_special(MDL_ADDR), q & 0xFFFF)
            confere(f"{nome} MDH (resto)", s.get_special(MDH_ADDR), r & 0xFFFF)
            confere(f"{nome} flags", flags(s),
                    {'Z': (q & 0xFFFF) == 0, 'N': bool(q & 0x8000), 'C': False, 'V': v})

# alguns valores fixos, conferidos à mão (não derivados da mesma fórmula)
img = montar_texto("        DIV R5\n")
s = roda(img, 5, -7, -100)                      # -100 / -7 = 14 resto -2
confere("DIV -100/-7", (s16(s.get_special(MDL_ADDR)), s16(s.get_special(MDH_ADDR))), (14, -2))
s = roda(img, 5, 7, -100)                       # -100 / 7 = -14 resto -2
confere("DIV -100/7", (s16(s.get_special(MDL_ADDR)), s16(s.get_special(MDH_ADDR))), (-14, -2))
s = roda(img, 5, -7, 100)                       # 100 / -7 = -14 resto 2
confere("DIV 100/-7", (s16(s.get_special(MDL_ADDR)), s16(s.get_special(MDH_ADDR))), (-14, 2))
img = montar_texto("        DIVU R5\n")
s = roda(img, 5, 7, 50000)                      # 50000 / 7 = 7142 resto 6
confere("DIVU 50000/7", (s.get_special(MDL_ADDR), s.get_special(MDH_ADDR)), (7142, 6))
img = montar_texto("        DIVLU R15\n")
s = roda(img, 15, 300, 1000000 & 0xFFFF, 1000000 >> 16)
confere("DIVLU 1000000/300", (s.get_special(MDL_ADDR), s.get_special(MDH_ADDR)), (3333, 100))
img = montar_texto("        DIVL R0\n")
m = -1000000 & 0xFFFFFFFF
s = roda(img, 0, 300, m & 0xFFFF, m >> 16)
confere("DIVL -1000000/300", (s16(s.get_special(MDL_ADDR)), s16(s.get_special(MDH_ADDR))), (-3333, -100))

# --- 3: bytes reais do firmware ---------------------------------------------
REAIS = [
    (0x3ADB6, bytes([0x5B, 0x55]), 'DIVU r5', 5),    # hub de curva 1D
    (0x3ADE0, bytes([0x4B, 0x22]), 'DIV r2', 2),
    (0x016AE, bytes([0x6B, 0xEE]), 'DIVL r14', 14),  # logo após "CMP r14,#0"
]
bin_fw = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Scenic 2.0 16v.bin')
if os.path.exists(bin_fw):
    fw = open(bin_fw, 'rb').read()
    for off, bs, _, _ in REAIS:
        confere(f"bytes do .bin em {off:#07x}", fw[off:off + 2].hex(), bs.hex())
dis_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'ferramentas_disassembly')
if os.path.exists(os.path.join(dis_dir, 'c166dis.py')) and os.path.exists(bin_fw):
    sys.path.insert(0, dis_dir)
    try:
        import c166dis
        for off, _, txt, _ in REAIS:
            confere(f"c166dis {off:#07x}", c166dis.decode_one(off)[2], txt)
    except Exception as e:  # ferramenta externa, não versionada: só avisa
        print(f"aviso: c166dis.py não conferido ({e})")

# "5B 55" com MDL=1000, R5=7: DIVU R5 -> 142 resto 6 (antes: divisor = SFR
# 0xFEAA, que lê 0 -> V=1 e MDL intacto)
s = roda(bytes([0x5B, 0x55]), 5, 7, 1000)
confere("real 5B 55 (DIVU R5)", (s.get_special(MDL_ADDR), s.get_special(MDH_ADDR), s.flags['V']),
        (142, 6, False))
# "4B 22" com MDL=-1000, R2=7: DIV R2 -> -142 resto -6 (antes: SFR 0xFE44)
s = roda(bytes([0x4B, 0x22]), 2, 7, -1000)
confere("real 4B 22 (DIV R2)", (s16(s.get_special(MDL_ADDR)), s16(s.get_special(MDH_ADDR))), (-142, -6))
# "6B EE" com MD=-70000, R14=1000: DIVL R14 -> -70 resto 0
m = -70000 & 0xFFFFFFFF
s = roda(bytes([0x6B, 0xEE]), 14, 1000, m & 0xFFFF, m >> 16)
confere("real 6B EE (DIVL R14)", (s16(s.get_special(MDL_ADDR)), s.get_special(MDH_ADDR), s.flags['Z']),
        (-70, 0, False))

# --- 4: nibbles diferentes são inválidos ------------------------------------
for opc in OPC.values():
    for b in (0xF5, 0x1D, 0x50):
        try:
            Sim(bytes([opc, b])).step()
            falhas.append(f"{opc:02X} {b:02X} deveria dar Trap (nibbles diferentes)")
        except Trap:
            pass

if falhas:
    print('\n'.join(falhas[:50]))
    print(f"... {len(falhas)} falha(s)")
    sys.exit(1)
print("OK: DIV/DIVU/DIVL/DIVLU na codificação real 'nn'")
