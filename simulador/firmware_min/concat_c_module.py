#!/usr/bin/env python3
"""Concatena vários `.h`/`.c` de `reimplementacao_c/` (já portados por
`port_c_typedef.py`) num único arquivo `.c`, porque `c167cc` não tem
pré-processador (sem `#include`, então cada módulo precisa ser 1 arquivo só
na hora de compilar) - remove linhas de diretiva de pré-processador
(`#ifndef`/`#define`/`#endif`/`#include`/`#ifdef`) e o bloco fixo
`extern "C" { ... }` (removido como par - senão uma chave solta sobra e
quebra a sintaxe, achado 21/08/2026 tentando concatenar kline_legacy pela
1ª vez).

Uso:
    python3 concat_c_module.py arq1.h arq2.h arq3.c ... > saida.c
"""
import re
import sys

EXTERN_C_OPEN = re.compile(r'#ifdef __cplusplus\s*\nextern "C" \{\s*\n#endif\s*\n?')
EXTERN_C_CLOSE = re.compile(r'#ifdef __cplusplus\s*\n\}\s*\n#endif\s*\n?')


def strip(text: str) -> str:
    text = EXTERN_C_OPEN.sub('', text)
    text = EXTERN_C_CLOSE.sub('', text)
    lines = [l for l in text.splitlines() if not l.strip().startswith('#')]
    return '\n'.join(lines)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    out = []
    for path in sys.argv[1:]:
        with open(path) as f:
            text = f.read()
        out.append(f"// ==== {path} ====")
        out.append(strip(text))
    sys.stdout.write('\n'.join(out) + '\n')
