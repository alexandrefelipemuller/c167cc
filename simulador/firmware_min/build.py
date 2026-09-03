#!/usr/bin/env python3
"""Concatena os fragmentos hand-authored de src/ (ordem numérica 00..99)
num único .asm e monta com c166asm.py - não existe linker de verdade (ver
plano do experimento), então "múltiplos arquivos" aqui é só concatenação
textual determinística antes de uma única chamada ao montador (que resolve
labels/vars num único passe sobre o resultado).

Uso:
    python3 build.py [saida.bin]
"""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(HERE, 'src')
OUT_ASM = os.path.join(HERE, 'firmware_full.asm')


def main():
    out_bin = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'firmware_full.bin')
    fragments = sorted(glob.glob(os.path.join(SRC_DIR, '*.asm')))
    if not fragments:
        raise SystemExit(f"nenhum fragmento .asm encontrado em {SRC_DIR}")
    print("fragmentos (nesta ordem):")
    for f in fragments:
        print(f"  {os.path.basename(f)}")

    with open(OUT_ASM, 'w') as out:
        for f in fragments:
            with open(f) as fh:
                out.write(fh.read())
                out.write("\n")

    asm_tool = os.path.join(os.path.dirname(HERE), 'c166asm.py')
    subprocess.run([sys.executable, asm_tool, OUT_ASM, out_bin], check=True)
    print(f"-> {OUT_ASM}")
    print(f"-> {out_bin}")


if __name__ == '__main__':
    main()
