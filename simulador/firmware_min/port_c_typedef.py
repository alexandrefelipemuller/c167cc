#!/usr/bin/env python3
"""Porte MECÂNICO de arquivos `.c`/`.h` de `reimplementacao_c/` pro
subconjunto de C que `c167cc` aceita - roda ANTES do compilador (diferente
de `port_real_abi.py`, que roda DEPOIS, no `.asm` já gerado). Só mexe na
SINTAXE da declaração de tipo, nunca na lógica/comportamento documentado:

  - remove toda linha `#include ...` (c167cc não tem pré-processador)
  - `typedef struct { ... } nome;` -> `struct nome { ... };`
  - `typedef enum { ... } nome;` -> `enum nome { ... };`
  - `typedef union { ... } nome;` -> `union nome { ... };`
  - toda ocorrência solta do nome (fora da própria declaração) vira
    `struct nome`/`enum nome`/`union nome` conforme o tipo original -
    inclusive em parâmetro de função, campo de struct, ponteiro etc.
  - `bool` -> `uint16_t`, `\btrue\b` -> `1`, `\bfalse\b` -> `0`
    (sem `<stdbool.h>` neste compilador)
  - `size_t` -> `uint16_t` (sem `<stddef.h>`)
  - remove `static` (`c167cc` não tem esse token no léxico - achado 21/08/2026
    tentando compilar kline_legacy.c pela 1ª vez; sem linker/múltiplas
    unidades de tradução de verdade nesta toolchain, "static" não muda nada
    de qualquer forma - toda função já é efetivamente arquivo-único)

Roda em MÚLTIPLOS arquivos de uma vez (precisa ser assim: um `typedef` que
mora no `.h` de um módulo é referenciado em `.c`/`.h` de outros módulos -
ver achado 21/08/2026 tentando portar `kline_legacy.h` sozinho, que referencia
`kwp_response_t`/`kwp_diag_state_t` typedef'd em `kline_dispatcher.h`. Por
isso a varredura de nomes typedef'd é feita sobre TODOS os arquivos passados
antes de reescrever qualquer um deles - senão uma referência cruzada fica
sem o prefixo `struct`/`enum`/`union` e o compilador não reconhece o tipo.

NÃO mexe em `memcpy`/`memset`/outras chamadas de `string.h` - essas
precisam ser revisadas e reescritas à mão como laço explícito, caso a
caso (o script só avisa em qual arquivo elas aparecem).

Uso:
    python3 port_c_typedef.py arq1.h arq1.c arq2.h ...          # imprime cada um, separado por "=== arquivo ==="
    python3 port_c_typedef.py --write arq1.h arq1.c arq2.h ...  # sobrescreve todos in-place
"""
import re
import sys


TYPEDEF_AGG_RE = re.compile(
    r'typedef\s+(struct|enum|union)\s*(\w*)\s*\{(.*?)\}\s*(\w+)\s*;',
    re.DOTALL,
)


def collect_names(text):
    names_by_kind = {'struct': set(), 'enum': set(), 'union': set()}
    for m in TYPEDEF_AGG_RE.finditer(text):
        kind, name = m.group(1), m.group(4)
        names_by_kind[kind].add(name)
    return names_by_kind


def port(text: str, names_by_kind: dict, path_for_warnings: str = '<stdin>') -> str:
    text = re.sub(r'^\s*#include\s*[<"][^>"]*[>"]\s*$', '', text, flags=re.MULTILINE)

    def repl(m):
        kind, _tag, body, name = m.group(1), m.group(2), m.group(3), m.group(4)
        return f'{kind} {name} {{{body}}};'

    text = TYPEDEF_AGG_RE.sub(repl, text)

    for kind, names in names_by_kind.items():
        for name in names:
            text = re.sub(rf'(?<!{kind} )\b{name}\b', f'{kind} {name}', text)

    text = re.sub(r'\bbool\b', 'uint16_t', text)
    text = re.sub(r'\btrue\b', '1', text)
    text = re.sub(r'\bfalse\b', '0', text)
    text = re.sub(r'\bsize_t\b', 'uint16_t', text)
    text = re.sub(r'\bstatic\s+', '', text)
    text = re.sub(r'\bextern\s+(?!"C")', '', text)  # (não mexe em `extern "C" {`,
                                              # removido à parte por concat_c_module.py)
                                              # sem múltiplas unidades de tradução de
                                              # verdade nesta toolchain (tudo concatenado
                                              # num arquivo só pra compilar, ver
                                              # concat_c_module.py) - "extern" também não
                                              # existe no léxico do c167cc
    text = re.sub(r'\bNULL\b', '0', text)  # sem <stddef.h>/#define, NULL não existe

    if re.search(r'\bmemcpy\b|\bmemset\b|\bstrlen\b|\bstrcpy\b', text):
        print(f"AVISO: {path_for_warnings} usa memcpy/memset/etc - "
              f"revisar e reescrever à mão como laço explícito antes de compilar",
              file=sys.stderr)

    return text


if __name__ == '__main__':
    args = sys.argv[1:]
    write = False
    if args and args[0] == '--write':
        write = True
        args = args[1:]
    if not args:
        raise SystemExit(__doc__)

    sources = {}
    all_names = {'struct': set(), 'enum': set(), 'union': set()}
    for path in args:
        with open(path) as f:
            sources[path] = f.read()
        names = collect_names(sources[path])
        for k in all_names:
            all_names[k] |= names[k]

    for path in args:
        ported = port(sources[path], all_names, path)
        if write:
            with open(path, 'w') as f:
                f.write(ported)
        else:
            print(f"=== {path} ===")
            print(ported)
