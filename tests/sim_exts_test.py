#!/usr/bin/env python3
"""EXTS Rw,#1 (opcode 0xDC, modo 00): codificação e acesso físico seg*0x10000+off
no c166sim.py. Uso: sim_exts_test.py <diretório simulador/>"""
import os, sys, tempfile
SIM_DIR = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else 'simulador')
sys.path.insert(0, SIM_DIR)
from c166asm import Asm  # noqa
from c166sim import Sim  # noqa

src = """
t:
	MOV R0, #2
	MOV R1, #0x1234
	MOV R2, #0x5A
	EXTS R0, #1
	MOVB [R1], R2
	EXTS R0, #1
	MOV R3, [R1]
	RET
"""
with tempfile.NamedTemporaryFile('w', suffix='.asm', delete=False) as f:
    f.write(src); caminho = f.name
try:
    asm = Asm(); asm.load_file(caminho)
    img = bytes(asm.assemble(org=0)[0])
finally:
    os.unlink(caminho)
assert bytes([0xDC, 0x00]) in img, "EXTS R0,#1 deveria ser DC 00"
s = Sim(img)
s.mem[0x21234] = 0; s.mem[0x21235] = 0
for _ in range(7):
    s.step()
assert s.mem[0x21234] == 0x5A, hex(s.mem[0x21234])
assert s.r[3] == 0x005A, hex(s.r[3])
print("OK")
