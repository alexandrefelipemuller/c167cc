#!/usr/bin/env python3
"""Porta a saída CRUA do c167cc (`--dump-asm`, ABI real de pilha com
`[R15+#N]`, PUSH/POP, ADD/SUB SP, CALLR) pra algo que `simulador/c166asm.py`
consegue montar - SEM reescrever a lógica (diferente de
`compiler/tests/port_to_toy_asm.py`, que joga fora o frame de pilha inteiro
e achata tudo em globais - isso aqui existe só porque, até esta sessão,
c166asm.py não tinha `[Rw+#offset]`; agora tem, então a única transformação
necessária é sintática/textual, não semântica):

  - remove diretivas `.section .text`/`.section .bss`/`.global NOME`
    (c166asm.py não usa seções)
  - converte `NOME: DS N` (`.bss`) em `RESERVE NOME, #N` - achado montando o
    primeiro teste de array real (21/08/2026): variável escalar simples
    (`DS 2`) já é auto-alocada certo na primeira vez que aparece num MOV (ver
    `note_var` em c166asm.py), mas um ARRAY (`DS 16` pra `uint16_t[8]`) NÃO -
    o compilador só referencia o array via endereço-base (`#TABLE`) + ponteiro
    calculado em runtime, nunca via um offset textual tipo `TABLE+14` que o
    `note_var` conseguiria enxergar sozinho pra saber que precisa de mais que
    2 bytes. Sem essa conversão, um array de 8 words ficava alocado com só 2
    bytes e o acesso a índices >0 vazava pra memória não reservada.
  - renomeia DIVU -> DIV (só o mnemônico com sinal existe neste montador -
    ver docs/limitations.md, ainda uma limitação real). MULU NÃO é mais
    renomeado (achado 02/09/2026: c166asm.py agora monta MULU de verdade,
    opcode 0x1B - renomear pra MUL mudava o resultado sempre que um operando
    tinha o bit 15 setado, invisível enquanto só a metade baixa do produto
    [MDL] era lida, mas quebrava a metade alta [MDH] usada pela leva de
    multiplicação com widening do c167cc).
  - `CALLR nome` -> `CALLA UC, nome` - achado 21/08/2026 compilando um
    módulo real (kline_legacy.c) pela 1ª vez: `CALLR` neste montador é
    relativo de 8 bits (±254 bytes, igual `JMPR` - já tinha forçado
    `JMPA` em vez de `JMPR` no `boot.asm` escrito à mão por isso), e uma
    função compilada de verdade facilmente passa desse alcance pra outra
    função do mesmo arquivo. `CALLA` é absoluto (sempre alcança, 4 bytes
    em vez de 2 - custo aceitável dado a prioridade do próprio projeto
    "correctness > simplicity > ... > optimization").
  - `JMPR cc, alvo` -> `JMPA cc, alvo` - mesmo motivo do item acima, achado
    21/08/2026 compilando `dtc_sirius32.c` (função grande o bastante pra um
    `if`/`for` interno saltar mais de ±254 bytes).

Uso:
    python3 port_real_abi.py entrada_full.asm > saida.asm
"""
import re
import sys


def port(asm_text: str) -> str:
    out = []
    for line in asm_text.splitlines():
        stripped = line.strip()
        if stripped.startswith('.section') or stripped.startswith('.global'):
            continue
        m = re.match(r'^(\w+):\s*DS\s+(\d+)', stripped)
        if m:
            out.append(f'RESERVE {m.group(1)}, #{m.group(2)}')
            continue
        line = re.sub(r'\bDIVU\b', 'DIV', line)
        m = re.match(r'^CALLR\s+(\S+)\s*$', stripped)
        if m:
            out.append(f'    CALLA UC, {m.group(1)}')
            continue
        m = re.match(r'^JMPR\s+(\S+)\s*,\s*(\S+)\s*$', stripped)
        if m:
            cc, target = m.group(1), m.group(2)
            cc = cc[3:] if cc.lower().startswith('cc_') else cc
            out.append(f'    JMPA {cc}, {target}')
            continue
        out.append(line)
    return '\n'.join(out) + '\n'


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    with open(sys.argv[1]) as f:
        sys.stdout.write(port(f.read()))
