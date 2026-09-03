# Montador + simulador C166/C167 — `simulador/`

Três peças, construídas do zero em 19/08/2026:

- **`c166asm.py`** — montador: `.asm` → `.bin` com opcodes reais da C166/C167 (mesma
  tabela de `ferramentas_disassembly/c166dis.py`).
- **`c166sim.py`** — simulador de instrução: executa um `.bin` (montado por
  `c166asm.py` OU um dump de firmware real) opcode a opcode.
- **`fat.asm` / `fall.asm` / `filter.asm`** — três programas de exemplo (fatorial,
  queda livre em ponto fixo Q8.8, filtro de sensor) usados como suíte de regressão.

Não são um simulador de propósito geral: cobrem só o que apareceu de verdade
rodando os três exemplos e explorando o boot real da Copa Clio (`../Clio 1.6 16v
(Copa Clio)`) — ver "Cobertura de opcodes" e "Limitações conhecidas" abaixo.

## Uso rápido

```bash
# montar
python3 c166asm.py fat.asm fat.bin

# rodar (direto no .bin, não monta nada)
python3 c166sim.py fat.bin --syms=fat.asm NUMERO=5

# sem --syms, endereços em hex cru em vez de nome de variável
python3 c166sim.py fat.bin 0x2A=5
```

`--syms=arquivo.asm` é só conveniência (chama `c166asm.py` pra extrair a tabela de
símbolos, sem tocar no `.bin`) — dá pra rodar qualquer `.bin` sem ele, inclusive um
dump de firmware real, passando endereços hex diretos.

## Arquitetura do `c166sim.py`

### Memória

- `self.mem`: `bytearray(0x1000000)` (16MB, 24 bits — cobre segmento `0x00`-`0xFF`).
- O `.bin` carregado é **espelhado em dois lugares**: offset físico `0` (janela de
  chip-select baixa, usada pelo vetor de reset e primeiras instruções do POST antes
  do bus controller ser reprogramado) e offset físico `0x100000` (janela onde a
  mesma flash aparece depois da reconfiguração de `SYSCON`/`ADDRSEL`/`BUSCON` — é a
  convenção `endereço da CPU = offset do arquivo + 0x100000` já usada em todo
  `ferramentas_disassembly/`). Sem o espelho em `+0x100000`, qualquer `CALLS`/`JMPS`
  pra segmento `!= 0x00` (a maioria do firmware real) ia parar em memória zerada.
- `self.pc` é sempre o endereço físico completo — não existe registrador `CSP`
  separado, ele é sempre `pc & 0xFF0000` (só trocamos de segmento via `JMPS`/`CALLS`,
  que setam `pc` explicitamente).
- **RAM interna/SFR/ESFR (`0xF000`-`0xFFFF`) começa zerada**, não com bytes do
  `.bin` — o `.bin` é um dump de FLASH, não inclui RAM; sem essa limpeza, variáveis
  de RAM liam lixo de flash em vez de começar em branco como no reset real.

### Paginação DPP

`translate_mem()` implementa o algoritmo real do manual Infineon (§6.2/§6.4):
sem override, os bits 15-14 do endereço lógico escolhem `DPP0-3` (SFR reais em
`0xFE00/02/04/06`, default de reset `0,1,2,3` = identidade); com `EXTP`/`EXTPR`
ativo, a página vem do operando da instrução; com `EXTS`/`EXTSR`, é um segmento
inteiro sem dividir em página de 16KB. `EXTP/EXTS/EXTPR/EXTSR/EXTR/ATOMIC`
(opcodes `0xDC`/`0xD7`/`0xD1`) setam uma janela `(mode, valor, count)` que dura N
instruções, decrementada em `step()` (não em `_step_inner`, pra não precisar tocar
nos ~30 pontos de retorno do dispatch).

O campo `reg` compacto (0x00-0xEF endereça SFR/ESFR em `0xFE00`/`0xF000` + 2×b,
0xF0-0xFF é GPR) e o campo `bitoff` (RAM/SFR/ESFR/GPR bit-addressable) são
**sempre físicos, nunca passam por DPP** — confirmado no manual (`00'FE00H`,
prefixo de página explícito).

### Mock de periférico (timer/ADC/status)

Simulador nenhum de instrução consegue saber sozinho quando um registrador é
hardware de verdade (avança sozinho) ou RAM comum (só muda se o software mudar).
Descoberto por tentativa e erro que "mockar tudo por padrão é pior que não mockar
nada" (ver histórico abaixo) — a versão final usa `manual_3286A/c167cr_userguide.pdf`
(o *User's Manual* de periféricos do C167CR, Tabela 22-4 — **diferente** do
`c166ism.pdf`/instruction-set manual usado no resto do projeto; achado consultando
esse documento a pedido do usuário, que suspeitou certo que alguns dos "timers"
podiam não ser timer nenhum) como fonte de verdade pra cada endereço, em vez de
deduzir só pelo padrão de uso:

| Endereço | Nome oficial (Tabela 22-4) | Tratamento | Por quê |
|---|---|---|---|
| `0xFE52` | T1 (CAPCOM Timer 1) | `TIMER_ADDRS`, corre livre | confirmado: timer de contagem livre de verdade, reset `0x0000` |
| `0xFE44` | T4 (GPT1 Timer 4) | `TIMER_ADDRS`, corre livre | idem |
| `0xFF1E` | ONES ("Constant Value 1's Register", só leitura) | `CONST_ADDRS`, fixo `0xFFFF` | não é timer — é constante de hardware, sempre `0xFFFF`. O mock incremental antigo só "funcionava" por sorte estatística (passava por `0xFFFF` 1x a cada 65536 leituras) |
| `0xFF1C` | ZEROS ("Constant Value 0's Register", só leitura) | `CONST_ADDRS`, fixo `0x0000` | irmão do ONES, mesma família — adicionado preventivamente, ainda não visto travando nada |
| `0xFF32` | PWMCON1 (PWM Module Control Register 1) | **sem mock**, registrador comum (`0x0000`) | registrador de CONFIG, reset `0x0000` — não corre sozinho. TEM escritas reais no binário (padrão OR/AND de bit), mas a rotina que escreve é despachada só por tabela+`CALLI` em runtime, sem chamador estático achável — ver "Limitações" |
| `0xFE88` | CC4 (CAPCOM Register 4) | **sem mock**, registrador comum (`0x0000`) | registrador de CAPTURA/comparação, reset `0x0000` — só muda por evento de hardware ou escrita explícita, não corre sozinho |
| `0xFEAA` | *(não implementado)* | sem mock, `0x0000` **é o valor real correto** | ver explicação abaixo |
| `0xFFBA` | *(não implementado)* | sem mock, `0x0000` **é o valor real correto** | idem |
| `0xEF00-0xEFFF` | Controlador CAN on-chip (CSR/IR/BTR/GMS/.../15 objetos de mensagem, manual §18) | `CONST_ADDRS`: `0xEF00`=`0x0001` (INIT=1, reset documentado), resto `0x0000` (idle/inválido) | janela decodificada como periférico, não como flash — sem isso, uma tarefa despachada por `TRAP` lia o Interrupt Register (`0xEF02`) como flash apagada (`0xFF`) e nunca via "sem interrupção pendente" |

**Sobre `0xFEAA`/`0xFFBA` — RESOLVIDO (19/08/2026)**: não são registrador
desconhecido nem bug — são **oficialmente SFR não implementado no chip real**.
Duas confirmações independentes:

1. O `c167cr_userguide.pdf` já usado no projeto **cobre explicitamente o
   C167SR-LM** ("About this Manual": lista `C167SR-LM - Version with PLL, 2 KByte
   XRAM` entre os derivados descritos) — é o mesmo chip da Copa Clio (marcação
   física `SAK-C167SR-LM/RM` fotografada + artigo acadêmico independente, ver
   `notas/RESUMO.md` §2.2-2.3). Não é "parente próximo", é o manual certo.
2. A Tabela 22-3/22-4 é descrita como listando **"all SFRs which are
   implemented"** — exaustiva, não uma amostra. E o manual documenta
   explicitamente o comportamento de endereço não implementado: *"Unused
   (E)SFR addresses are reserved for future members of the C166 Family"* e
   *"Non-implemented (reserved) SFR bits cannot be modified, and will always
   supply a read value of '0'"*.

Conclusão: **`0x0000` já É o valor real e correto** pra esses dois endereços —
o simulador (memória comum, sem mock, default `0`) já reproduz o hardware real
fielmente.

**Sobre a divisão por zero em si — RESOLVIDO (19/08/2026)**: inicialmente
supunha-se que isso disparava um **trap de Classe A** de hardware (teoria
errada, corrigida depois de reler o manual com atenção). O texto correto,
`c167cr_userguide.pdf` p.4-16 (seção "ALU Status"): *"Note that a division by
zero will always cause an overflow."* Ou seja: **não existe trap nenhum pra
divisão por zero** — nem na Tabela 5-2 (Hardware Trap Summary) há qualquer
entrada de "divide by zero". O hardware real simplesmente seta `V=1`/`C=0` e
segue em frente; o conteúdo exato de `MDL`/`MDH` depois disso não é
especificado no manual (resultado de um algoritmo shift-subtract interno com
divisor 0). O simulador agora reproduz isso: `DIV`/`DIVU`/`DIVL`/`DIVLU` com
divisor 0 setam `V=1`/`C=0` e deixam `MDL`/`MDH` intocados, sem abortar.

**Histórico da investigação** (relevante pra quem for mexer nisso de novo):
1ª versão mockava a janela `0xF000-0xFFFF` inteira automaticamente — corrompeu uma
constante de calibração fixa e quebrou um march test de RAM (`CMP r12,[r0]`
write-then-verify). 2ª versão trocou pra lista branca com incremento por leitura
(baseado só em "nunca escrito pelo firmware" + padrão de uso) — funcionou o
suficiente pra render 1,4M+ instruções de exploração, mas tratava `ONES`/`PWMCON1`/
`CC4` errado (fazia constante e registrador de config "correrem livre", o que
contradiz o hardware real). 3ª versão (atual) usa o manual de periféricos como
fonte de verdade em vez de dedução — mais correto, mas para de progredir mais cedo
(391k em vez de 1,4M instruções) porque `0xFF32`/`0xFE88` agora ficam honestamente
em zero até o init real ser encontrado, em vez de fingir um valor.

### `_sfr_written`/breg/regfield

`read_regfield16`/`write_regfield16` e `read_breg_field`/`write_breg_field` cobrem
o campo `reg` compacto pra word e byte respectivamente (incluindo o caso SFR
direto, `b<0xF0`); `mem_read16`/`mem_write16`/`mem_read8`/`mem_write8` cobrem o
endereçamento `mem` de 16 bits (com DPP); todos passam pelo mock automaticamente.

## Cobertura de opcodes

~85 opcodes reais implementados (de um universo de 236 documentados — `ferramentas_disassembly/README.md`
lista os 20 opcodes sem entrada nenhuma na tabela, alguns reservados/indefinidos
no próprio manual). Grupos:

- **MOV** — todas as formas word/byte: registrador, imediato (`#data4`/`#data8`/`#data16`),
  memória direta, indireta (`[Rw]`, `[Rw+]`, `[-Rw]`, `[Rw+#data16]`),
  mem↔mem, mem↔[Rw]. `MOVBZ`/`MOVBS` (zero/sign-extend) em todas as variantes.
- **Aritmética/lógica** — `ADD/ADDC/SUB/SUBC/CMP/AND/OR/XOR` e as versões `B`
  (byte) em TODAS as formas de endereço (`Rw,Rw`, `reg,mem`, `mem,reg`,
  `reg,#imm`, forma curta `ind` de 2 bytes com `#data3`/`[Ri]`/`[Ri+]`).
- **Multiplicação/divisão** — `MUL`/`MULU`/`DIV`/`DIVU`/`DIVL`/`DIVLU`.
- **Shift** — `SHL`/`SHR`/`ASHR`, com as flags exatas do manual (`C`=último bit
  que saiu, `V`=OR de todos os bits que passaram pelo carry durante o shift).
- **Comparação com contador** — `CMPI1`/`CMPI2`/`CMPD1`/`CMPD2` (idioma de loop `FOR`).
- **Bit** — `JB`/`JNB`/`JBC`/`JNBS`, `BSET`/`BCLR`, `BAND`/`BOR`/`BXOR`/`BCMP`/`BMOV`/`BMOVN`,
  `BFLDH`/`BFLDL` (campo de bits com máscara).
- **Controle de fluxo** — `JMPR` (16 condições completas), `JMPA`/`CALLA`
  (mesmo segmento), `JMPI`/`CALLI` (indireto, mesmo segmento), `JMPS`/`CALLS`/`RETS`
  (troca de segmento, empilha CSP+IP), `CALLR`/`RET` (mesmo segmento), `PUSH`/`POP`.
- **Paginação** — `EXTP`/`EXTS`/`EXTPR`/`EXTSR` (forma registrador e imediata),
  `EXTR`/`ATOMIC`.
- **Diversos** — `NEG`/`CPL` (word e byte), `SRVWDT`/`EINIT`/`SRST` (no-op),
  `PRIOR` (normalização de ponto flutuante).
- **Interrupção/trap** — `TRAP` (software) e a interrupção de hardware do CC26
  (ver "Validação"), com `RETI` compartilhado entre as duas.

### GPRs (`r0-r15`) são uma janela de CP na IRAM, não uma lista fixa

Achado 19/08/2026 (ver "Validação", item 6): o C166 real não tem registradores
de propósito geral fixos — são 16 words na IRAM começando no endereço do
Context Pointer (CP, SFR `0xFE10`). `self.r` é a classe `_RegisterBank`
(redireciona `self.r[n]` pra `mem[CP+2*n]`), não uma lista Python solta. Isso
importa pra quem for mexer no simulador: mudar CP (via `SCXT` ou escrita
direta) muda instantaneamente o que `r0-r15` significam, exatamente como no
hardware real — não existe "salvar registrador" separado de "salvar CP".

- **Trap por software** — `TRAP #n` (empilha `PSW`/`CSP`/`IP`, desvia pro vetor
  `n*4` no segmento 0) e `RETI` (desfaz na ordem inversa). `PSW` é reconstruído
  só a partir das flags ALU (`N`/`C`/`V`/`Z`); os bits de `ILVL`/`IEN`/`HLDEN`
  não são modelados porque nada no simulador ainda dispara interrupção real
  (só `TRAP` por software chega nesse caminho).

Não implementado (fora do escopo desta rodada): interrupções de hardware de
verdade (vetor de periférico, prioridade `ILVL`/`IEN`), `PCALL`, `EXTS`/`EXTSR`
forma imediata (só a forma registrador foi validada contra firmware real),
formas `bitbit`/`indirect` mais exóticas nunca vistas em uso.

## Bugs reais achados (não só no simulador — em `ferramentas_disassembly/c166dis.py`,
a tabela de opcodes compartilhada por todo o projeto de RE)

Cada um foi confirmado contra a imagem real da Copa Clio (via Ghidra com o módulo
C166 já instalado em `/opt/ghidra`, ou rastreando o próprio simulador). Detalhes
completos nos comentários do código-fonte, aqui só o resumo:

1. **`is_byte_mn()`** usava `mn.endswith('B')` — falso positivo em `SUB` (termina
   em B por coincidência). Trocado por lista branca.
2. **Modelagem RL/RH inteira errada**: a fórmula usada desde 01/08 (`nibble
   0-7=RLn, 8-15=RHn`) está incorreta — a real é `regnum=nibble>>1, seleção
   L/H=nibble&1`. Afeta `MOVB/ADDB/SUBB/ANDB/ORB/XORB/CMPB/CPLB/NEGB` — **67
   referências a `RLn`/`RHn` nas notas do projeto podem estar citando o
   registrador errado**, não remediado (fora do escopo desta rodada).
3. **`Rwd4`/`Rbd4`** (`MOV Rw,#data4`, `SHR/SHL/ASHR/ROL/ROR Rw,#data4`): nibble
   alto e baixo invertidos (registrador e imediato trocados).
4. **`MOVBZ`/`MOVBS Rwn,Rbm`**: única forma "mn" ao contrário de toda a tabela
   (nibble alto=fonte, baixo=destino, não o padrão "nibble alto=n=primeiro
   operando" usado em todo o resto).
5. **`JB`/`JNB`/`JBC`/`JNBS`** (`bitaddrrel`): byte do deslocamento relativo e
   byte da posição do bit estavam trocados.
6. **`DIV`/`DIVU`/`DIVL`/`DIVLU`/`NEG`/`CPL`** (kind `Rw`/`Rb`): sempre
   imprimiam registrador cru, escondendo quando o operando era SFR direto
   (achado porque `DIVU 0xFEAA` aparecia como `DIVU r5`).
7. **`trace.py`/`cfg_trace.py`** (ferramentas de dataflow, não o desmontador):
   tinham a correção OPOSTA do item 3, aplicada um dia antes — nibble alto como
   destino pra `Rwd4` também, quando é baixo. Corrigido pra bater com o item 3.

E no próprio `c166sim.py` (bugs que não existiam no desmontador, só na execução):
`SHL`/`SHR`/`ASHR` nunca atualizavam a flag `C` (causava loop infinito de verdade
num march test real); `cc_true()` só cobria 9 das 16 condições; `MOV` (todas as
~23 variantes) nunca atualizava `Z`/`N` (o manual documenta que atualiza, e o
dispatcher de diagnóstico real depende disso: `MOV r4,0xF7F0` seguido de
`JMPR cc_Z` testa a flag do próprio `MOV`).

## PSW (SFR `0xFF10`) nunca sincronizada com `self.flags` na leitura via bit (02/09/2026)

Achado investigando (a pedido do projeto irmão `Sirius32/`) por que `file 0x3B82C`/`0x3B850`/
`0x3B954` (família "biblioteca_aritmetica_saturada", ~180 chamadores estáticos combinados)
ficavam bloqueados pra promoção: eram rotinas de soma/subtração saturada que testam Carry/
Overflow com `JNB 0xFF10.1,alvo` logo depois de `ADD`/`SUB`/`ADDC`, e o resultado do simulador
não batia com o esperado dado o carry real da operação.

**Causa**: o PSW é uma SFR mapeada em `self.mem[0xFF10:0xFF12]`, mas nada nunca ESCREVIA ali -
os bits N/C/V/Z só existem de fato em `self.flags['N'/'C'/'V'/'Z']`, atualizados por
`update_flags_add()`/`update_flags_sub()`. `JMPR cc_XX` funciona porque `cc_true()` lê
`self.flags` direto. Mas `JB`/`JNB`/`BSET`/`BCLR`/`BAND`/`BOR`/`BXOR`/`BCMP`/`BMOV`/`BMOVN`
(qualquer instrução que testa um bit de `bitoff`) passam por `bitoff_word()` -> `w16(0xFF10)`,
que lê a memória crua - sempre estável (0, ou lixo de escrita direta que nunca acontece), então
o bit testado NUNCA refletia a flag real. Confirmado empiricamente: `ADD r12,r13` com
`r12=0xFFFF,r13=1` (carry real=1, deveria saturar em `0xFFFF`) seguido de `JNB 0xFF10.1` tomava
o MESMO desvio que `r12=1,r13=1` (carry real=0) - o simulador devolvia `R4=0x0000` (errado,
sem saturar) em vez de `R4=0xFFFF`.

**Layout de bits confirmado** (não bastava a doc genérica da Infineon - confirmado contra 2
fontes independentes já presentes no próprio projeto): bit0=N, bit1=C, bit2=V, bit3=Z. Essa é
exatamente a fórmula que `_enter_trap()`/`RETI` já usavam pra empilhar/desempilhar PSW no
TRAP e na interrupção de hardware simulada (já validada pela suíte de regressão) - e bate com
`notas/notas_desmontagem.md` linha 603 ("usa o carry do PSW (`0xFF10.1`)") e com o `GLOSSARIO.md`
("bit1=C").

**Correção**: `bitoff_word()` agora, ao resolver o endereço pra `0xFF10` (constante `PSW_ADDR`),
devolve um word SINTETIZADO a partir de `self.flags` (`_psw_word()`) em vez de ler
`self.mem` cru. Mudança cirúrgica - só a leitura da SFR PSW especificamente é afetada; nenhum
outro endereço muda de comportamento.

**Limitação deliberada (fora de escopo desta correção)**: só a LEITURA de `0xFF10` via
`bitoff_word()` (o caminho de `JB`/`JNB`/`BAND`/etc.) foi sintetizada. ESCRITA em PSW por essas
mesmas instruções (`BSET`/`BCLR`/`set_bitoff_word()`) e leitura genérica via `w16()`/`MOV
Rn,0xFF10` continuam batendo na memória crua - nenhuma instância disso foi encontrada testando
PSW no firmware real até agora; se aparecer, tratar do mesmo jeito.

**Validação**: rodando `file 0x3B850` direto do `Scenic 2.0 16v.bin` no simulador corrigido,
`r12=0xFFFF,r13=1` (carry=1) agora devolve `R4=0xFFFF` (satura corretamente) e `r12=1,r13=1`
(carry=0) devolve `R4=0x0002` (soma normal) - dois desvios DIFERENTES, como devia ser. Suíte de
regressão do projeto irmão (`Sirius32/scripts/regressao_core.py`) continua `OK: 79`, sem
divergências novas.

## Validação

- **Regressão**: os 3 `.asm` de exemplo continuam com os mesmos resultados numéricos
  (`fat`: 5!=120; `filter`: média=20; `fall`: física Q8.8 batendo com cálculo de
  referência independente) depois de cada mudança nesta sessão.
- **Firmware real**: o boot da Copa Clio roda do reset (`pc=0`) até dentro do
  superloop — POST completo (bus controller, watchdog, 3 testes de RAM reais),
  cadeia de dispatch de diagnóstico K-line (SID `0x27`/SecurityAccess testado com
  uma chamada sintética retornando limpo via `RETS` real). Com a lista branca de
  mock atual (baseada no manual de periféricos, mais correta que a versão anterior
  por dedução), a exploração passou de **391.289** pra **mais de 5.000.000 de
  instruções reais** (limite do driver de teste, não um travamento) depois de uma
  sequência de 4 correções em 19/08/2026:
  1. Divisão por zero: não trapeia, só seta `V=1`/`C=0` e segue (RESOLVIDO — ver
     seção acima).
  2. `TRAP`/`RETI`: o próprio `DIVU 0xFEAA` corrigido revelou um `TRAP #0x40`
     real do firmware logo depois (despacho de tarefa por software, não bug).
  3. `SCXT` (opcode `0xC6`, "switch context" — empilha `reg`, carrega imediato):
     não implementado, bloqueava a entrada da tarefa despachada pelo `TRAP`.
  4. **Registradores do controlador CAN on-chip (`0xEF00-0xEFFF`)**: a tarefa
     despachada pelo `TRAP #0x40` lê o Interrupt Register do CAN (`0xEF02`)
     esperando `0` (sem interrupção pendente) pra sair do loop — o simulador
     devolvia os bytes crus do `.bin` nesse endereço (flash apagada, `0xFF`,
     porque a janela `0xEF00-0xEFFF` é decodificada como PERIFÉRICO no hardware
     real, não como flash — manual §18 "Organization of Registers and Message
     Objects"), então a condição de saída nunca era satisfeita. Mockado com
     valores de reset/idle plausíveis (`CONST_ADDRS`, ver comentário no código) —
     confirmado contra `notas_desmontagem.md` (POST testa exatamente essa janela
     em `0x1c6e`, "teste dos registradores do CAN", e uma nota anterior já linkava
     `0xEF00` = CSR do CAN1).
  5. **Interrupção de hardware de verdade (CC26/CAPCOM Register 26, SFR `0xFE74`,
     trap `0x3A`, vetor `0xE8`)**: implementada pra destravar o semáforo
     `0xFD9A.15` (`JNB $,$` em `file 0x1805C`) — um tick sintético
     (`_hw_timer`, 1/instrução) dispara `_enter_trap` por igualdade de 16 bits
     contra CC26, igual um "compare match" de hardware de verdade. Respeitando
     o manual: não reentra em si mesma (hardware real não deixaria uma
     interrupção de mesma prioridade se preemptar — `ILVL` não modelado, mas o
     efeito é replicado com uma guarda de profundidade de pilha) e não dispara
     no meio de uma janela `EXTP`/`EXTS`/`EXTR`/`ATOMIC` ("Instructions EXTP
     and EXTS inhibit interrupts the same way as ATOMIC", manual). CC26 é
     pré-armado no boot com um valor plausível (simplificação documentada — a
     rotina real que arma isso, `file 0x3B0AA`, fica atrás de um call site que
     a exploração ainda não alcança sozinha).
  6. **Bug arquitetural raiz: GPRs não eram uma janela de CP na IRAM** —
     achado rastreando a ativação da interrupção acima: um valor de `r2`
     completamente absurdo (`0xFE07`, corrompendo `DPP3`) apareceu no meio de
     um teste do POST. Rastreamento mostrou que o C166 real NÃO tem
     registradores de CPU fixos: `r0-r15` são uma janela de até 16 words na
     IRAM, começando no endereço do **Context Pointer** (CP, SFR `0xFE10`,
     manual §3.2: "the Context Pointer (CP) register determines the base
     address of the currently active register bank"). `SCXT` (usado em TODO
     handler de interrupção/trap deste firmware) troca CP pra dar a cada
     tarefa seu PRÓPRIO banco, sem precisar salvar/restaurar registrador por
     registrador. O simulador representava `self.r` como uma lista Python
     solta, sem ligação nenhuma com CP — toda troca de contexto via `SCXT` era
     um no-op silencioso, e tarefas completamente diferentes ficavam
     silenciosamente compartilhando e corrompendo o mesmo `r0-r15`. Substituído
     por `_RegisterBank` (classe que redireciona `self.r[n]` pra
     `mem[CP+2*n]`) — corrigiu a corrupção por completo, sem precisar tocar em
     nenhum dos ~140 lugares que já usavam `self.r[i]`/`self.r[i]=val` (só a
     classe por trás do atributo mudou).
  7. **`PRIOR Rwn,Rwm`** (opcode `0x2B`): não implementado — instrução real
     de normalização de ponto flutuante (manual §21.5, "aids in normalizing
     floating point numbers by indicating the position of the first set bit").
     `Rwn` recebe a contagem de deslocamentos à esquerda pra normalizar `Rwm`
     (0 se o bit 15 já está em 1, 15 se só o bit 0 está setado); `Rwn=0`/`Z=1`
     se `Rwm=0`.
  Depois da correção 6 (a mais impactante — corrigia uma premissa arquitetural
  errada em todo o simulador, não um opcode isolado), a exploração passou de
  travada permanentemente no semáforo `0xFD9A.15` pra **837.780 instruções**
  até o próximo opcode faltando (`PRIOR`, corrigido em seguida), e depois disso
  pra **mais de 5.000.000 de instruções sem nenhum erro**. Bloqueio atual: uma
  `JMPS seg=0x11,$` auto-referente em `file 0x110320` — um dos "loops-armadilha"
  já documentados no projeto (`notas_desmontagem.md`: só 8 dos 128 vetores
  reais têm handler, os outros são vetores não-atribuídos que travam de
  propósito) — nível de trap/vetor 0x44 (CAPCOM Register 29). Ainda não
  investigado se é um disparo espúrio nosso ou comportamento real esperado
  nesta configuração.

## Conserto do heurística "NOP = fim de programa" (20/08/2026)

A heurística antiga (`op==0xCC` retornava `False` de `step()`, sinalizando
halt) foi **removida por completo** — NOP agora é sempre uma instrução real
(2 bytes, não faz nada, `pc+=2`), igual no hardware de verdade. Fim de
programa passou a ser detectado em `Sim.run()`: `pc` parado no MESMO endereço
por `_HALT_THRESHOLD` (10.000) steps consecutivos — a mesma convenção que o
próprio firmware real usa pros vetores de interrupção não atribuídos (loop
auto-referente, ver `notas_desmontagem.md`, "loops-armadilha"). O threshold
de 10k (não 1) existe porque um busy-wait condicional de espera de hardware
de verdade (ex.: `JNB $,$` esperando o semáforo `0xFD9A.15`, ver "Validação"
item 5 abaixo) também fica com `pc` parado por várias iterações até a
interrupção liberar o bit — 1 repetição sozinha não distingue "esperando"
de "parado de propósito para sempre"; 10k é bem maior que qualquer período de
espera conhecido no projeto (ex. os 250 ciclos do CC26).

`fat.asm`/`filter.asm` (os 2 exemplos que tinham `NOP` como marcador de fim)
foram atualizados pra terminar num loop auto-referente explícito
(`HALT: JMPR cc_UC, HALT` / reaproveitando o label `END` já existente) em vez
de `NOP`.

**Efeito colateral encontrado e corrigido junto**: com o halt exigindo várias
iterações em vez de parar instantaneamente, o mock de interrupção de hardware
CC26 (pré-armado incondicionalmente em `Sim.__init__`, dispara ~250 steps
depois do reset) passou a disparar também nos programas de teste pequenos —
que não têm tabela de vetores real carregada naquela região (`0x00E8`), então
o desvio de `pc` ia parar em memória crua/colidindo com a pilha, travando com
opcode inválido (`pc=0xFBFA` — o mesmo sintoma que já tinha sido flagrado
antes rodando `fall.asm`, ver comentário em `_check_hw_timer_interrupt`).
Antes, isso nunca aparecia porque o halt-por-NOP parava a simulação bem antes
dos 250 steps. Conserto: o pré-arme de CC26 em `__init__` agora só acontece
se a imagem carregada tiver um vetor de reset real (`image[0] == 0xFA`,
opcode `JMPS`) — real hardware também não dispararia essa interrupção num
programa sintético sem nenhuma inicialização.

Validado: os 3 `.asm` de regressão rodam limpos até o halt real (sem bater no
limite de passos por engano); os 4 mapas em `../mapas/` não caem mais em
memória de lixo — Copa Clio e Scenic agora páram corretamente no wait real
(`pc=0x11805C`, o `JNB $,$` do semáforo CC26 já documentado) em vez de rodar
cego até o limite artificial de steps; `Clio RS1 GrN.ori` avançou até
`PCALL` (não implementado, fora do escopo desta rodada); `Clio 1.6 16v.bin`
avançou até `pc=0x10010` antes de bater de novo no opcode reservado `0x44`
(problema de origem/reset ainda não resolvido, ver seção acima).

## Otimizações de desempenho (20/08/2026) - ~12% mais rápido, sem mudar comportamento

Pedido do usuário: achar melhorias de desempenho fáceis/óbvias e seguras.
Processo: cópia de segurança (`c166sim.py.bak_pre_perf`) antes de tocar em
nada; perfilado com `cProfile` sobre 400k instruções reais (`Scenic 2.0
16v.bin`) pra achar os gargalos de verdade em vez de chutar; cada mudança
validada comparando **hash SHA-256 da memória inteira + `pc` final + erro**
entre a versão nova e a antiga, rodando os mesmos 4 mapas de `../mapas/`
por 300k instruções cada - **idêntico byte a byte em todos os 4**, nenhuma
mudança de comportamento.

1. **Opcodes mais frequentes promovidos pro topo do dispatch em
   `_step_inner`**: a função é uma cadeia sequencial de `if op == X`/
   `if op in (...)` (sem tabela de despacho) - o custo de achar o opcode
   certo é proporcional a quantos `if` vêm antes dele. Medido quais opcodes
   dominam um trecho real de boot (`CMP reg,#data16`=10,6%, `AND
   reg,#data16`=7%, `SRVWDT/EINIT/SRST`=4,3%, `SHL Rw,#data4`=3,3%,
   `BFLDL`=2%) e movidos pro início da função, logo depois do `NOP`/`JMPR`
   (que já eram baratos). Reordenar é seguro aqui porque os blocos são
   **mutuamente exclusivos por construção** (cada valor de `op` só bate em
   um bloco) - mover não muda qual bloco executa, só quão rápido ele é
   achado. Bloco original removido do lugar antigo (sem duplicação de
   código).
2. **`_RegisterBank.__getitem__`/`__setitem__` (acesso a `r0-r15`) - inline
   de `w16()`/`set_w16()`**: é o caminho mais quente do simulador inteiro
   (quase 1 chamada por instrução, cada uma fazia 2 chamadas de método
   encadeadas pra ler `CP` e depois o registrador). Trocado por leitura
   direta de `self.sim.mem[...]` - mesma conta, sem a camada de função a
   mais. Cortou as chamadas a `w16()` de ~1,46M pra ~630k num trecho de
   teste de 400k instruções.
3. **`_check_hw_timer_interrupt`: guardas de reentrância/`EXTP` movidos pra
   ANTES da leitura de `CC26`** - evita 1 leitura de memória desnecessária
   quando uma IRQ já está rolando ou uma janela `EXTP`/`EXTS`/`EXTR`/
   `ATOMIC` está ativa (comum - toda instrução paginada usa `EXTP`).

**Não feito** (avaliado, mas fora do critério "fácil e seguro"): converter
`_step_inner` inteira numa tabela de despacho por opcode (ganho bem maior
em teoria, mas exigiria fatiar ~1300 linhas de `if`s em métodos separados -
risco de introduzir bug bem mais alto pra uma sessão de "otimização
segura"); cache de `DPP`/`CP` (risco de invalidação incorreta em algum
ponto de escrita não coberto).

## Ponte OBD2 via TCP (ELM327) - `obd2_bridge.py` (20/08/2026)

Pedido do usuário: conectar um app de scanner OBD2 real contra o firmware
rodando no simulador (não a reimplementação em C - ver `../reimplementacao_c/`,
que é lógica reconstruída à mão, responder lá não provaria nada sobre o
binário real). Progresso desta sessão, do mais concreto ao mais em aberto:

### ✅ Periférico ASC0 (UART) real implementado em `c166sim.py`

`UART_TBUF_ADDR`/`UART_RBUF_ADDR`/`UART_TIC_ADDR`/`UART_RIC_ADDR`/
`UART_CON_ADDR`/`UART_BG_ADDR` (endereços confirmados no manual, tabela
22-3) + `Sim.uart_inject_rx_byte()`/`Sim.uart_pop_tx_bytes()`. Achado-chave
que simplificou tudo: **o firmware não usa vetor de interrupção pra ASC0** -
faz *polling* de `S0RIC.S0RIR`/`S0TIC.S0TIR` no superloop
(`notas_desmontagem.md`, "Polling de UART"), então simular certo é só
mexer nos registradores certos - o firmware descobre sozinho. Testado
isoladamente (byte TX cai na fila + seta `S0TIR`; byte injetado aparece em
`S0RBUF` + seta `S0RIR`) e sem regressão nos 3 `.asm`.

### ✅ `obd2_bridge.py` - servidor TCP com emulador ELM327 mínimo

`KLineTransport` (fala só bytes crus com o `Sim`) + `ELM327Session` (traduz
AT commands/hex ASCII sobre TCP). Handshake ELM327 testado e funcionando
(`ATZ`/`ATE0`/`ATSP0` respondem certo). Arquitetura documentada no próprio
arquivo pra trocar por bytes crus depois (pedido do usuário: "se no futuro
eu quiser bytes crus"). Formato de quadro K-line usado é um PALPITE (ISO
14230-2 placeholder, mesmo já usado em `reimplementacao_c/kline/` porque o
formato real nunca foi decifrado, ver `DECODIFICACAO.md`) - se o firmware
não responder, pode ser esse palpite errado, não necessariamente falha do
mapa.

### 🟡 Handshake de wakeup decodificado (estados 0-3, `file 0x1e30`-`0x1fa0`)

Sem pulso de fast-init real (decisão do usuário: pular direto pro sync
`0x55`), a requisição de teste voltou `NO DATA` - investigando por quê,
achado que o dispatcher do handshake (`0xFC5D`, tabela `file 0x39A`) nunca
saiu do estado 0 porque o pino do K-line (`0xFFC4.7`, sampleado em
`file 0x1e30`) nunca fica "alto" sem ajuda externa. Estado 2 (`file 0x1e94`)
mede tempo decorrido e, se CURTO, pula direto pro estado 8 (a via de
fast-init, sem decodificar byte de endereço 5-baud) - acionar isso não
precisa de timing real calibrado em ms, só do pino ficando alto rápido o
suficiente (não precisa saber a taxa exata de polling).

**Achado que trava isso por enquanto**: forçar `0xFFC4.7=1` DESDE O BOOT
perturba o POST/boot (a execução diverge pra um caminho diferente do normal
antes mesmo de chegar no superloop). Próximo passo óbvio (não feito ainda):
deixar o firmware bootar 100% sem interferência, achar o ponto de idle
real, e só DEPOIS começar a mexer no pino - simulando um tester que conecta
depois da central já ligada, não durante o boot.

### 🔴 Bloqueio real, achado investigando o item acima: NENHUM dos 4 mapas chega no superloop operacional em simulação

Rodando o boot 100% limpo (sem tocar em nada) do Scenic: passa o POST,
chega no wait do semáforo CC26 (`pc=0x11805C`, já documentado) por volta de
800k instruções, **consegue sair dele** (a interrupção CC26 funciona - ao
contrário do que se temia investigando isso hoje), mas cai direto em
**outro** loop-armadilha, `pc=0x110320`, por volta de 1M instruções, e
trava ali pra sempre. **Esse ponto já era conhecido ANTES desta sessão**
(seção "Validação" mais abaixo neste README, "Bloqueio atual... JMPS
seg=0x11,$ auto-referente em file 0x110320") - não é novo, só ficou mais
claro que é um bloqueio ESTRUTURAL que impede qualquer teste de K-line, não
um problema do handshake em si.

**Achado extra investigando `0x110320` agora - RESOLVIDO (20/08/2026)**:
não é uma tabela nova nem um bug de simulação - é **o vetor de hardware
REAL número `0x44`** (CAPCOM29, endereço `0x000110` na tabela de 128
vetores da CPU, físico `0x0000-0x01FF` - a mesma tabela já confirmada
128/128 limpa). Vetor `0x44` faz parte dos "vetores não atribuídos que
travam de propósito" já documentados no projeto (`JMPS seg=0x11,0x0320`,
self-referente). Rastreado com o simulador instrumentado (capturando o
`pc` anterior a cada transição) até a causa raiz, determinística, sem
ambiguidade:

- `file 0x180C0-0x180FC`: rotina real que faz housekeeping (flags em
  `0xFD9C`, um checksum simples em `0xF7AC`, chama 2 sub-rotinas de
  subsistema `0x77D8`/`0x8376`) e termina com `CALLS seg=0x13,0xB072`
  (helper genérico de cópia de contexto, sempre retorna via `RETS` normal).
- A instrução IMEDIATAMENTE seguinte ao retorno dessa chamada, **sem
  nenhum desvio possível** (sequência reta, sem `JMPR`/`JB` entre elas), é
  `TRAP #0x44` (`file 0x18100`) - incondicional.
- `TRAP #0x44` desvia pro vetor real `0x000110` → `JMPS seg=0x11,0x0320` →
  `0x110320`, o self-loop.
- Confirmado que mesmo preso nesse loop o sistema continua "vivo" em baixo
  nível: outras interrupções reais (ex. um handler de tick que alimenta o
  watchdog em `0xFFA0` e grava timestamp de `T1` em `0xF9BE`) continuam
  disparando e retornando normalmente via `RETI` direto pro `0x110320` -
  só o fluxo do PROGRAMA PRINCIPAL trava ali de propósito, o sistema de
  interrupções/watchdog continua rodando.

**Conclusão**: isso é comportamento determinístico REAL do firmware -
`TRAP #0x44` está ali no binário, incondicional, e não há nenhum jeito de
pular por engano. Não é bug do simulador nem evidência de mapa corrompido
- estamos replicando fielmente.

### Quem chama a rotina em `file 0x180C0` - RASTREADO (20/08/2026)

Reconstruída a pilha de chamadas completa (cada `CALLS` empilha CSP+IP;
lida direto de `sim.mem` no momento do trap, sem precisar de breakpoint
opcode a opcode):

```
file 0x18060  CALLS seg=0x11, 0x7EB4   ; chama o LAÇO PRINCIPAL (file 0x17EB4)
  ↳ dentro do laço principal, 1ª iteração:
  file 0x17EE4  CALLS ...              ; chama subsistema
    ↳ file 0x13710C  CALLS ...         ; mais um nível (segmento 0x13)
      ↳ file 0x13AFF6  CALLS seg=0x11, 0x8066  ; chama a rotina que termina em TRAP
```

Ou seja: a rotina do trap é chamada de dentro da **1ª iteração do próprio
laço principal**, não de algum código de boot/teste incomum - é parte do
fluxo normal de inicialização de subsistema logo que o superloop começa a
rodar.

**Achado mais importante - o ponto de decisão real** (`file
0x180A6-0x180BC`, a função que PRECEDE a rotina do trap): decide se o
fluxo cai no trap ou desvia por cima dele:

```
file 0x180AA  MOVB RL4, 0xF7AC        ; lê um contador/checksum (incrementado
                                       ; a cada rodada via ADDB 0xF7AC,0xFF1E)
file 0x180AE  JMPR cc_Z, 0x180B8      ; se F7AC==0, pula
file 0x180B0  JB  0xFD9C.11, 0x180B8  ; se bit 11 setado, pula
file 0x180B4  JNB 0xFD9C.9,  0x180E4  ; se bit 9 LIMPO -> desvia por CIMA
                                       ; do trap inteiro, pra file 0x180E4
file 0x180B8  JNB 0xFD9C.11, 0x180C4
file 0x180BC  JB  0xFD9C.9,  0x180D0
                                       ; senão (bit 9 setado) cai na rotina
                                       ; que termina em TRAP #0x44
```

**O bit `0xFD9C.9` é quem decide** se esse caminho de "trap deliberado" é
tomado ou não.

### Quem seta/limpa `0xFD9C.9` - RASTREADO (20/08/2026)

As 3 rotinas mencionadas antes (`file 0x77D8`/`0x8376`/`0x811C`) **não são
as responsáveis** por esse bit especificamente (decompiladas: `0x8376`
mexe em `0xFD9C.7`, não `.9`; `0x77D8` mexe em `0xFDAC.3/.11` e
`0xFD9C.13`; `0x811C` é flash apagada nos primeiros bytes, região de dado
não-código). Em vez de seguir decompilando na mão, escaneei o `.bin`
inteiro por TODO acesso de bit a `0xFD9C` (134 ocorrências) e depois
confirmei com o simulador instrumentado (registrando toda mudança real do
bit durante a execução) - achado direto, sem ambiguidade:

- **Setado 1x** por uma função em `file 0x27D06` (`BSET 0xFD9C.9`, logo em
  seguida `BSET 0xFD60.12` e um cálculo de índice de tabela a partir de
  `r14` - parece "agendar uma operação pra um canal/índice").
- **Deveria ser limpo** por uma ISR de verdade (`SCXT`+`RETI` confirmados)
  em `file 0x16DA8` - mas essa ISR **nunca dispara** na nossa simulação
  (não bate com nenhum dos 8 vetores reais que têm handler distinto na
  tabela de 128 vetores - achado escaneando a tabela inteira - então deve
  ser alcançada por algum mecanismo indireto ainda não identificado).
- Confirmado com o simulador: o bit fica SETADO pra sempre depois de
  `file 0x27D06` rodar (~step 781k) e nunca volta a `0`, exatamente o
  padrão que faz o código de decisão (`0x180B4`) sempre cair no caminho do
  trap.

**RETRATADO (20/08/2026)**: a hipótese "isso é rotina de EEPROM" (por causa
dos literais `0x46A0`/`0x4650` caírem numericamente na faixa
`0x4000-0x7FFF`) estava **errada** - conferido que a ISR usa `EXTR_ATOMIC`
(SFR estendido, `0xFF00-0xFFFF`) antes da comparação, não `EXTP`/`EXTS`
(o mecanismo real de paginação de EEPROM, já auditado à exaustão em
`notas_desmontagem.md` §16 - 91/91 instâncias, zero em página EEPROM, por
dois métodos independentes, **`RESUMO.md` já fecha isso com "zero
evidência de código de escrita de EEPROM em todo o binário"**). Não
contradizer essa conclusão - `CMP r0, #0x46A0` é só comparação com uma
constante qualquer, coincidência numérica, não pista de EEPROM. O que a
ISR realmente representa continua em aberto (detalhes/ressalva completa em
`notas_desmontagem.md` §0e).

**Hipótese revisada (sem alegar EEPROM)**: o bit `0xFD9C.9` representa
algum tipo de "operação pendente" (natureza exata desconhecida) - setado
ao agendar em `file 0x27D06`, limpo quando a ISR de conclusão dispara.
Real hardware provavelmente vence essa corrida sempre; nosso simulador não
sabe disparar essa interrupção (mecanismo de trigger real desconhecido),
então o bit nunca é limpo e o código sempre cai no trap "deveria nunca
acontecer".

### ISR implementada (`_check_pending_op_isr`, 20/08/2026) - dispara certo, mas NÃO evita o trap sozinha

Implementado disparo sintético da ISR real (`PENDING_OP_ISR_TARGET =
0x116DA8`, mesmo padrão pragmático do CC26: sem saber o evento de hardware
real que aciona, dispara o CÓDIGO REAL um tempo depois do bit `0xFD9C.9`
ser setado, deixando a própria lógica da ISR decidir o que fazer). Achado
consertando: a ISR faz `SCXT 0xFE10,#0xFAEA` (troca de banco) e testa
`CMP r0,#0x46A0; JMPR cc_C,...` - `cc_C` aqui é ULT (menor-que sem sinal),
então com `r0=0` (default) o desvio ia pro caminho `CALLI [r1]` indireto
por um ponteiro sem motivo pra ser válido - **1ª tentativa mandou a
exploração pra memória aleatória** (`pc` crescendo monotonicamente por
flash/RAM não inicializada). Consertado pré-carregando `r0` (=
`mem[0xFAEA]`) com `0x46A0` antes de disparar, forçando o caminho "seguro"
(limpa flags, sem call indireto) - validado: a ISR agora dispara, limpa
`0xFD9C.9` de fato (confirmado instrumentando toda mudança do bit), e não
corrompe mais nada.

**Mas o trap ainda acontece** (~step 838k, mesmo tempo de antes). Achado
rastreando mais fundo: a decisão em `file 0x180A6-0x180BC` não depende só
de `0xFD9C.9` - tem um contador (`0xF7AC`, decrementado via
`ADDB 0xF7AC,0xFF1E`) que, quando chega a **`0`, desvia por um caminho
DIFERENTE** (pula os checks de `0xFD9C.11`/`.9` na ordem normal, cai direto
em `JNB 0xFD9C.11,0x180C4` - que como `.11` nunca é setado em lugar nenhum
do binário exceto DENTRO do próprio caminho do trap, sempre leva ao trap
`independente do estado de 0xFD9C.9`). `F7AC` começa em `0` (RAM zerada) e
só é incrementado DEPOIS de escapar com sucesso pela primeira vez - ou
seja, **a 1ª vez que essa checagem roda, já cai direto no caminho ruim**,
antes de qualquer coisa poder incrementar `F7AC`. Testado forçar
`F7AC != 0` manualmente desde o boot - **não resolveu** (o trap ainda
acontece no mesmo lugar), sinal de que `F7AC` está sendo resetado de volta
a `0` por outro código antes de chegar na checagem, ou minha leitura do
fluxo de `0x180A6-0x180E4` ainda tem um furo não identificado.

### CONCLUSÃO FINAL, corrigindo a análise acima (20/08/2026) - o trap é INCONDICIONAL

Implementei um mock pragmático pra nunca deixar `0xF7AC` chegar a `0`
(mesmo estilo dos mocks de `CC26`/`ONES`/`ZEROS` - intercepta na LEITURA
via `_mock_read`, não na escrita, porque `mem_write8` sempre grava o valor
real em `self.mem` depois de `_mock_write` chamar, sobrescrevendo qualquer
tentativa de rearmar lá). Testado: **não mudou nada** - o trap continuou
acontecendo exatamente no mesmo step. Investigando por que, rastreei
instrução a instrução os ~190 steps entre a checagem em `file 0x180A6` e
o `TRAP #0x44`, e a resposta é definitiva:

**`0x180E4` (o destino de TODOS os "escapes" - via `0x180B4` com bit9
limpo, OU via `0x180BC`→`0x180D0` com bit9 setado) FAZ FALLTHROUGH DIRETO
pra dentro de `0x180E8`→`0x180EC`→`file 0x18066`'s cadeia→...→`0x1180FC`→
`TRAP #0x44`.** Não existe branch nenhum que pula por CIMA dessa
sequência - toda a "decisão" em `0xFD9C.9`/`.11`/`F7AC` só decide QUAIS
efeitos colaterais acontecem no caminho (setar/limpar `0xFD9C.11`/`.12`,
rearmar `F7AC`), **nunca SE o trap acontece**. Confirmado rodando o
"escape" até o fim: ele passa por trabalho real (inclusive o handler de
CC26 disparando no meio do caminho, RETI incluso) e **ainda assim** volta
e executa o mesmo `TRAP #0x44` no final, sem exceção.

**Conclusão honesta, revisando a sessão anterior**: minha hipótese de que
esses bits "decidiam" evitar o trap estava **errada** - eles não têm esse
poder. O trap é comportamento **incondicional e deliberado** desta rotina
específica (`file 0x18066-0x18102`) sempre que ela é chamada - e ela É
chamada, na 1ª iteração real do superloop (cadeia de `CALLS` já
documentada acima). A ISR implementada (`_check_pending_op_isr`) e o mock
de `0xF7AC` continuam válidos como infraestrutura (código real, sem
corromper nada, testado) mas **não resolvem nem podem resolver** esse
trap específico - ele não é condicional nos termos que eu tinha mapeado.
Não implementada "escrita de flash" - a pista de EEPROM que motivava isso
foi retratada (ver acima), sem relação confirmada com este mecanismo.

**Em aberto pra quem quiser continuar**: já que o trap é incondicional
nessa rotina, a única forma de evitá-lo seria a rotina NUNCA SER CHAMADA -
o que exigiria rastrear ainda mais pra trás (por que o superloop decide
chamar `file 0x118066` logo na 1ª iteração) e entender se, em hardware
real, existe algum motivo pra essa chamada nem acontecer nessa forma
(ex.: um valor de config/ADC que muda o fluxo do superloop antes de
chegar aqui). Sem essa peça, o trap `0x44` é um limite genuíno da
exploração desta sessão, não um bug a mais pra corrigir no simulador.

**Efeito colateral achado nessa mesma investigação**: o halt-detector do
`Sim.run()` (ver seção "Conserto do heurística NOP" acima) teve um FALSO
POSITIVO num teste anterior desta sessão - reportou "parou" com
`pc=0x11805C` (o wait do semáforo) quando na verdade a execução escapa
dali e trava de verdade mais na frente (`0x110320`). O `_HALT_THRESHOLD`
de 10.000 aparentemente não é robusto o suficiente em todos os casos -
não corrigido ainda, fica pra próxima sessão junto com o item do
`0x110320`.

**Conclusão prática**: `obd2_bridge.py` está pronto e funcional
mecanicamente (UART real + ELM327 + TCP), mas testar contra o firmware "vivo"
de verdade esbarra num bloqueio ANTERIOR e não relacionado ao K-line -
nenhum dos 4 mapas atualmente alcança o ponto onde o polling de UART
rodaria em condições normais de operação.

## `ROR Rw,#data4` implementado (opcode `0x3C`, 20/08/2026)

Família `ROL`/`ROR` inteira não tinha NENHUMA forma implementada antes. Só
`0x3C` (`ROR Rwn,#data4`, formato `3C #n` no manual) foi implementado, por
ser o único da família que a exploração real bateu até agora (parava
`Clio RS1 GrN.ori` logo depois do conserto do `CMPI1/2/D1/2`, ver seção
acima). `0x0C`/`0x1C`/`0x2C` (`ROL Rw,Rw`/`ROL Rw,#data4`/`ROR Rw,Rw`)
continuam não implementados - mesmo helper (`_rotate`, ao lado de `_shift`)
já dá pra cobrir os três se aparecerem.

Semântica (manual, "ROR — Detailed Description"): gira `op1` à direita
`count` vezes, bit 0 entra no bit 15 E no Carry a cada passo. Flags: `Z`/`N`
pelo resultado final; `C` = último bit que saiu (limpo se `count==0`); `V` =
OR de todos os bits que passaram pelo Carry durante o giro (igual ao
`SHR`/`ASHR` em `_shift` - mesmo "flag de arredondamento"). Campo `#n`/`Rw`
usa a convenção `Rwd4` já estabelecida no resto do simulador (nibble alto =
imediato, baixo = registrador destino - `b&0xF`/`(b>>4)&0xF`).

Validado: unidade isolada (`ROR r0,#4` sobre `0x1234` → `0x4123`, flags
batendo com o manual) e suíte de regressão sem mudança. `Clio RS1 GrN.ori`
não trava mais em `0x3C` - passa direto pra `PCALL` (opcode ainda não
implementado, ver "Não implementado" acima - obstáculo diferente, não
investigado nesta rodada).

## `PCALL reg,caddr` implementado (opcode `0xE2`, 20/08/2026)

Manual ("Push Word and Call Subroutine Absolute", format `E2 RR MM MM`, 4
bytes): empilha o valor de `reg` (campo compacto de sempre - GPR se
`RR>=0xF0`, SFR direto em `0xFE00+2*RR` caso contrário, via
`read_regfield16`) e depois o IP de retorno, nessa ordem - IP fica no topo
da pilha (SP menor), pra bater com `RETP` (que despilha IP primeiro, `reg`
depois - `RETP` continua não implementado, não apareceu ainda). Desvia pro
`caddr` absoluto NO MESMO SEGMENTO (intra-segment, como `CALLA`/`JMPA` - o
manual não menciona troca de segmento aqui, diferente de `CALLS`/`JMPS`).
Flags: `Z`/`N` conforme o valor empilhado de `reg` (`V`/`C` não afetados,
`E` não modelado - mesma limitação já aceita pro resto do simulador).

Validado: unidade isolada (GPR e SFR direto, ordem de push/valores de
pilha, `Z`/`N`) e suíte de regressão sem mudança. **`Clio RS1 GrN.ori` não
trava mais em opcode nenhum** - roda os 6.000.000 de instruções completos do
teste (limite artificial, não travamento real), parando em `pc=0x1FF66`
ainda dentro do limite de passos.

## Exploração dos 4 mapas em `../mapas/` (20/08/2026)

Rodada pedida pelo usuário pra checar a alegação do tuner de que os 4 dumps
originais em `../mapas/` **não foram embaralhados por WinOLS**. Ver
`../mapas/README.md` pra regra de não mexer nesses arquivos sem copiar antes.

**Achado prévio necessário**: o `run()`/CLI padrão trata *qualquer* opcode
`NOP` (`0xCC`) como "fim de programa" (heurística só válida pros `.asm` de
teste, que terminam em NOP de propósito) — isso derrubava `Copa Clio` e
`Scenic` em **2 instruções**, parando bem em cima de um NOP de padding real
(`file 0x19F8`, entre dois `MOV` de configuração de `DPP`/`CP`, não fim de
nada). Rodada refeita tratando NOP como instrução normal (skip, não halt) pra
dar comparação justa — é isso que está na tabela abaixo. Vale considerar
consertar esse heurística no `run()` padrão (hoje é surpresa silenciosa: quem
rodar firmware real pela CLI sem saber disso vai achar que travou em 2
instruções).

| Mapa | Instruções rodadas | Onde parou | Vetores em offset 0 (`0xFA`×128) |
|---|---|---|---|
| `Clio 1.6 16v (Copa Clio)` | 6.000.000 (limite do teste, ainda rodando) | `pc=0x110320` — mesmo loop-armadilha já documentado (vetor de interrupção não atribuído, `JMPS seg=0x11,$` auto-referente) | 128/128 ✅ |
| `Scenic 2.0 16v.bin` | 6.000.000 (limite do teste, ainda rodando) | `pc=0x110320` — idêntico ao Copa Clio, mesmo endereço exato | 128/128 ✅ |
| `Clio RS1 GrN.ori` | 845.870 | bug real do simulador (ver abaixo) em `pc=0x00FC1C` | **0/128** ❌ |
| `Clio 1.6 16v.bin` | 395 | opcode `0x44` (reservado no manual, ver abaixo) em `pc=0x0216` | **0/128** ❌ |

### Correção importante: a conclusão "sem embaralhamento" NÃO vale pros 4 igualmente

Copa Clio e Scenic têm a tabela de vetores canônica limpa em offset 0 (128
entradas `JMPS`, `0xFA ss ll hh` cada 4 bytes) — igual ao que já era
conhecido/validado no projeto. `Clio RS1 GrN.ori` e `Clio 1.6 16v.bin` **não
têm isso**: offset 0 nesses dois decodifica como uma tabela de DADOS (pares
`C0 FA xx yy` repetidos — parece tabela de bit-endereço de porta/pino, não
código), idêntica entre os dois arquivos byte a byte nessa região. Testado e
descartado que seja um deslocamento constante simples (`shift` de ±1 a ±4
bytes contra Copa Clio não melhora o alinhamento; comparação byte-a-byte total
entre `Clio 1.6 16v.bin` e Copa Clio: só 30% dos bytes batem na mesma posição,
igual ruído de fundo).

Conclusão honesta: **offset de arquivo 0 provavelmente não é o endereço de
reset da CPU** nesses dois dumps (motivo ainda não investigado — pode ser
dump começando de um endereço físico diferente, cabeçalho/prefixo a mais no
arquivo, ou banco de flash diferente). Isso significa que os 845.870
instruções "de sucesso" do RS1 rodaram a partir de uma origem (`pc=0`)
provavelmente ERRADA — o simulador só não travou cedo porque a sequência de
bytes decodificada por acaso formou instruções válidas (JMPS/CALLS
encadeados) por um bom tempo antes de esbarrar num opcode que o simulador não
sabe tratar. **Não é evidência forte de integridade do arquivo** — é só
"não travou rápido", diferente da validação real feita pra Copa Clio (POST
completo reconhecido, poços de RAM testados, dispatcher K-line real). Pra
resolver isso de verdade, falta achar o endereço de reset real desses dois
arquivos (varrer por uma tabela de 128×`0xFA` em outro offset, não achada nos
primeiros 8KB testados) antes de repetir a exploração.

### Os 2 opcodes que pararam a exploração

**`0x44` (parou `Clio 1.6 16v.bin` em `pc=0x0216`)** — consultado
`imagens_siemens/c166ism.pdf`, Table 1 ("Instruction Overview ordered by
Hex-Code, lower half"): linha `x4`, coluna `4x` = **"–"** (reservado, sem
instrução definida no C166 real). **Não é uma lacuna de cobertura do
simulador** — é um opcode que não existe. Bater nele é sintoma de decodificar
a partir do endereço errado (ver seção acima: offset 0 desse arquivo não é
código de verdade), não motivo pra "implementar" nada. Só faz sentido revisitar
depois de achar o endereço de reset real do arquivo.

**`CMPI1`/`CMPI2`/`CMPD1`/`CMPD2 Rw,mem`** (opcodes `0x82`/`0x86`/`0x92`/`0x96`/
`0xA2`/`0xA6`/`0xB2`/`0xB6`, parava `Clio RS1 GrN.ori` em `pc=0x00FC1C` com
`TypeError`) — **CONSERTADO (20/08/2026)**. Os handlers em `c166sim.py`
(`_step_inner`, blocos dos opcodes acima) faziam `_, n =
self.regfield_addr(regb)` e assumiam `n` sempre um GPR válido (`self.r[n]`).
Mas `regfield_addr()` (linha 370) só devolve um `n` não-`None` quando
`regb >= 0xF0`; pra `regb < 0xF0` (campo `reg` compacto endereçando um SFR
direto, ex. `0xFE00 + 2*regb`) devolve `(addr, None)` — exatamente o caso já
tratado no resto do simulador (`read_regfield16`/`write_regfield16`, ver seção
"`_sfr_written`/breg/regfield" acima) mas que esses 4 opcodes específicos não
tratavam, batendo `self.r[None]` e explodindo com `TypeError`. Trocado o
acesso direto a `self.r[n]`/`self.r[n]=...` nesses 2 blocos (forma `#data16` e
forma `mem`) por `read_regfield16`/`write_regfield16` (mesmo padrão já usado
nos outros ~140 lugares do arquivo). Validado: suíte de regressão (`fat`/
`filter`/`fall`) sem mudança de resultado; exploração do `Clio RS1 GrN.ori`
avançou de 845.870 pra **846.452 instruções**, parando agora num opcode
DIFERENTE e não relacionado (`0x3C` = `ROR Rw,#data4`, válido no manual, só
ainda não implementado — não investigado nesta rodada).

## `EQU` implementado no `c166asm.py` (02/09/2026, a pedido do projeto irmão `Sirius32/`)

Achado integrando o outro repo do usuário (`Sirius32/`, reimplementação
independente do mesmo firmware a partir de rotinas-folha decifradas por
disassembly, ~70 arquivos em `core/` no momento): todo arquivo lá usa
`@ram(endereço)` do `c167cc` pra RAM/SFR fixo, o que gera uma linha
```
nome    EQU    0E2FAH        ; @ram @0xE2FA (uint16_t)
```
por símbolo (ver `compiler/docs/memory-model.md`) — e o montador não sabia
nada sobre `EQU`, só `RESERVE`/`DS`. Sem isso, `nome` virava mnemônico
desconhecido (maiusculado) e a linha inteira quebrava o parser (`AssertionError:
tamanho desconhecido p/ NOME [...]`). Como `firmware_min/` (a única coisa que
já compilava .c de verdade até agora) não usa endereço fixo — `dtc_sirius32.c`/
`crc16_sirius32.c` operam só sobre struct/parâmetro — esse gap nunca tinha
aparecido antes.

Corrigido em `c166asm.py`: nova diretiva `EQU` (`self.equs`, checada em
`note_var`/`resolve_mem_addr` antes de alocar como variável comum), com
parser pro formato Intel-hex que o `c167cc` emite (`0E2FAH`, dígito inicial
0 + sufixo H). Validado com 2 módulos reais de `Sirius32/core/` (reset de
flags simples e um par de rotinas com `CALLA` entre si + array `@ram` +
parâmetro-ponteiro, incluindo um caso de 2 arrays `@ram` que se sobrepõem
de propósito no endereço real — o simulador reproduziu a interação correta
entre as duas passadas). Ver `Sirius32/scripts/compilar_e_montar.py` e
`Sirius32/scripts/rodar_funcao.py` pro pipeline do lado de lá.

## `MULU` assemblado de verdade no `c166asm.py` (02/09/2026, mesma integração com `Sirius32/`)

`c166sim.py` já sabia EXECUTAR `MULU` (opcode `0x1B`, multiplicação sem
sinal — ver "Cobertura de opcodes" acima) desde antes desta sessão, mas
`c166asm.py` só sabia MONTAR o mnemônico `MUL` (`0x0B`, com sinal);
`firmware_min/port_real_abi.py` cobria esse buraco renomeando `MULU`→`MUL`
incondicionalmente ao portar a saída do `c167cc`. Isso é semanticamente
ERRADO sempre que um dos operandos tem o bit 15 setado (`MUL` interpreta
como negativo, `MULU` não) — só ficou invisível até agora porque a metade
baixa do produto (MDL, em `FE0Ch`) é bit-a-bit idêntica nos dois casos;
quebra assim que algo lê a metade alta (MDH, `FE0Eh`), que é exatamente o
que a leva de multiplicação-widening do `c167cc` (ver `compiler/docs/
limitations.md`, achado no mesmo dia) passou a precisar. Corrigido
adicionando o mnemônico `MULU` de verdade em `c166asm.py` (mesma
codificação de operando de `MUL`, opcode `0x1B` em vez de `0x0B`) e
removendo a renomeação em `port_real_abi.py` — `DIVU`→`DIV` continua
renomeado ali, é uma limitação separada e ainda não corrigida (o
montador nunca ganhou um caminho de codificação pra `DIVU`/`DIVL`/`DIVLU`
de verdade, só o `DIV` com sinal).

## `firmware_min/` — firmware próprio compilado de `reimplementacao_c/` (21/08/2026)

Experimento separado de tudo acima: um firmware PRÓPRIO (não o real da Copa
Clio), mínimo, que fala o mesmo protocolo K-line/KWP2000, com pelo menos um
módulo real (a tabela de DTC) genuinamente **compilado** de
`reimplementacao_c/dtc/dtc_sirius32.{h,c}` pelo `c167cc` deste repositório
(`compiler/`) — não hand-written — rodando de ponta a ponta no `c166sim.py`
e validado via quadro K-line real (`firmware_min/functional_test.py`) e via
`bridge_min.py` (ponte TCP pra um app ELM327 real, equivalente ao
`obd2_bridge.py` desta seção, mas pra esse firmware próprio, não pro
firmware real da Copa Clio). Documentação completa, pipeline C→binário,
lista de bugs de compilador/montador achados portando um módulo real, e
status módulo-a-módulo: `firmware_min/README.md`.

## Ponte OBD2/K-line (`obd2_bridge.py`) — investigação de formato de quadro (20/08/2026)

Sessão pediu pra investigar por que a ponte ELM327↔K-line (ver docstring de
`obd2_bridge.py`) sempre dava "NO DATA" com o app OBD2 real. Progresso real,
mas SEM fechamento total — ver "Bloqueio final" no fim desta seção.

**1. Formato de quadro real decifrado** (antes só um palpite ISO14230-2
genérico) — decompilado `file 0x3474`/`0x3504` (tabela `file 0x3BA`, 7
estados da camada física de recepção, indexada por RAM `0xFC66`):

```
[FMT] [TGT] [SRC=0xF1] [payload...] [checksum]
```
- `FMT` = `0x80|tamanho` OU `0xC0|tamanho`, escolhido pelo bit de calibração
  `0xFDFE.2` (mesmo bit que escolhe a variante de key byte no handshake,
  `file 0x39A` estado 5) — **`Scenic 2.0 16v.bin` usa `0xFDFE.2==0` →
  variante `0x80`** (confirmado rodando o binário real, não só lendo o
  código)
- `TGT` = `0x33` só é aceito no ramo `FDFE.2==1`; no ramo `FDFE.2==0` (o
  real, aqui) o `TGT` não é checado na DETECÇÃO do cabeçalho, mas a
  ACEITAÇÃO final da mensagem (`file 0x3564`) exige `TGT==0x7A` (ou `0xFF`
  em modo broadcast, gated por `0xFDFE.1`) — `0x33` é reusado como byte de
  sync do handshake fast-init, não é o endereço de quadro nesta variante
- `SRC` fixo `0xF1` (endereço padrão de ferramenta de diagnóstico,
  convenção ISO14230/SAE — checado sempre, nos dois ramos)
- `checksum` = soma 8-bit de tudo antes dele (igual ao palpite antigo, só
  que agora com cabeçalho endereçado de verdade)

Testado byte a byte contra o binário real: cabeçalho reconhecido, payload
armazenado, checksum bate, e o firmware real seta `0xFD06.13` ("mensagem
pronta") — **confirmado com trace de instrução, não só inferência**.

**2. Dois bugs de timing na ISR sintética do ASC0-RX corrigidos** (a ISR real
não é despachada por um controlador de interrupção genérico no simulador —
ver `_check_asc0_rx_isr` em `c166sim.py`, mesmo molde do `_check_pending_op_isr`
já existente):
- **Byte sobrescrito antes de ser lido**: com um número FIXO de passos entre
  bytes injetados, a ISR (que tem atraso variável por causa da guarda de
  não-reentrância compartilhada com o CC26) podia não ter rodado ainda
  quando o PRÓXIMO byte era injetado, sobrescrevendo `S0RBUF` — o 1º byte do
  quadro (`FMT`) sumia sempre. Corrigido esperando ativamente a ISR
  terminar de verdade (`_wait_byte_consumed()`, espera `S0RIR` baixar E
  nenhuma IRQ em voo, não um contador fixo).
- **Disparo duplo da mesma ISR pro mesmo byte**: a lógica de retry (que
  tenta de novo a cada passo quando a guarda de reentrância bloqueia)
  disparava a ISR de novo mesmo depois dela já ter limpo `S0RIR` sozinha,
  lendo `S0RBUF` já sobrescrito pelo PRÓXIMO byte. Corrigido checando se
  `S0RIR` ainda está setado antes de efetivamente disparar (`c166sim.py`,
  `_check_asc0_rx_isr`).

Sem os 2 acima, o registrador de deslocamento de 3 bytes do decodificador de
cabeçalho (`file 0x3474`) nunca via a sequência certa — testado byte a byte
com trace de instrução até confirmar o "banco" de registrador (`0xFCD6..`)
acumulando exatamente `[FMT,TGT,SRC]` no momento certo.

**3. Watchdog recalibrado** (`WDT_TICK_NUMERATOR`/`WDT_TICK_DENOMINATOR` em
`c166sim.py`, antes 1 tick por instrução) — achado testando o boot completo
com watchdog ligado: o teste de march de RAM do POST (`file 0x1B44-0x1BDE`,
`WDTREL=0xB6`, orçamento de 18944 ticks) só re-serve `SRVWDT` de verdade
**36504 ticks** depois (medido rodando o boot real com o reset
temporariamente desligado) — quase o DOBRO do orçamento com a aproximação
antiga. Consistente com o WDT real ticar a `fCPU/2` enquanto essa região do
código (bastante `EXTP`/acesso indexado) tem CPI médio bem acima de 2.
Recalibrado pra 1 tick a cada 2 instruções (proporção 1:2, margem de ~3.8%
sobre os 36504 necessários) — o self-loop de trap conhecido (`file
0x110320`) continua resetando corretamente, só que em 131072 passos em vez
de 65536 (ainda determinístico e finito, só mais lento).

**4. `obd2_bridge.py` ganhou retry automático de watchdog reset**
(`MAX_RESET_RETRIES`, `KLineTransport.request()`) — achado que o boot real
passa por MÚLTIPLOS resets de watchdog em sequência antes de estabilizar (o
self-loop + o march test, possivelmente mais), e cada reset LIMPA a flag de
mensagem pronta (`0xFD06.13`) antes do escalonador ter chance de consumir
ela (mesmo com a RAM preservada — a sequência de reboot limpa flags
pendentes como parte da reinicialização normal). A ponte agora reenvia o
quadro automaticamente se detectar um reset no meio da espera de resposta
(mesmo comportamento que um scanner OBD2 de verdade teria, retentando).

**5. Causa raiz de verdade encontrada: `file 0x1E0C` nunca é chamado em
NENHUM boot simulado, nem 1 vez em 500 mil passos** — não era mais um bug
de timing nem de RAM pulada. Rastreando o chamador (`file 0x1DF2→0x249E`),
achado que o firmware tem **dois escalonadores mutuamente exclusivos**,
escolhidos 1x por boot num self-check (`file 0x19C2`, RAM `0xFC19`), já
documentado estaticamente em `reimplementacao_c/scheduler/README.md`
("RAMO A" vs "RAMO B") mas nunca confirmado dinamicamente até agora:

| | RAMO A (`0xFC19=0x10`) | RAMO B (`0xFC19=0x30`) |
|---|---|---|
| Aciona K-line | ❌ Não | ✅ Sim (`0x1DF2→0x249E→0x1E0C`) |
| Nossa simulação cai aqui por padrão | ✅ sempre | nunca, sem forçar |

A decisão (`file 0x2C54`) lê 1 byte de calibração no endereço LÓGICO
`0xBFF2` (físico real `0x10FFF2`, via `DPP2` — **achado 20/08/2026**: um
poke direto em `mem[0xBFF2]` não tem efeito nenhum, porque esse endereço só
existe traduzido por `DPP`, igual o mesmo tipo de bug já visto com
`FC5D`/`BFF2`/etc.; a poke tem que ir no endereço físico certo,
`sim.translate_mem(0xBFF2)`). No `Scenic 2.0 16v.bin`, esse byte vale
`0xF0` no momento da decisão → seleciona RAMO A sempre. Forçando
`mem[0x10FFF2]=0xFF` bem antes da checagem (`file 0x19C2`), o firmware
real entra no RAMO B (`file 0x323E`) genuinamente, sem nenhum outro hack de
RAM — confirmado rodando o binário de verdade, `FC19` vira `0x30`.

**6. Novo bloqueio, mais profundo (não resolvido)**: dentro do RAMO B, logo
na entrada (`file 0x3308-0x3336`), o firmware espera um valor acumulado de
ADC (`0xFEA0`, 8 amostras somadas em `0xFC6C`, média em `0xFC6E`) atingir
um patamar (`0x8E`=142) antes de prosseguir — sem ADC real simulado
(`0xFEA0` sempre lê `0`), esse loop nunca progride e roda pra sempre (~40
mil passos por tentativa, sem limite de tentativas visível). Testado forçar
o valor diretamente (`r4=0x8E` na hora do `CMP`) só pra ver o que
acontece: o firmware real toma o caminho de **`SRST`** (auto-reset por
software) nesse caso — ou seja, `FC6E>=0x8E` não é "sucesso", é "desisti,
reseta". A semântica real (o que esse acumulador representa — tensão de
alimentação? referência de ADC batendo? outra coisa?) não foi decifrada.
Reverter esse bloqueio exigiria simular um ADC de verdade com uma curva de
subida plausível, o que sai do escopo de K-line/OBD2 e vira um projeto à
parte (monitoramento de alimentação/brownout). **Investigação da ponte
K-line pausada aqui** — os achados de formato de quadro, ISR e watchdog
continuam válidos e reaproveitáveis; falta só isso pra fechar o teste
ponta a ponta.

## Limitações conhecidas

- **Sem interrupção de hardware real** — vetor de periférico, prioridade
  `ILVL`/`IEN` não implementados. `TRAP` (software) e `RETI` já funcionam
  (19/08/2026), mas nada dispara uma interrupção de hardware de verdade ainda.
- **Despacho indireto (`CALLI` via tabela) não resolvido** — vários pontos do
  firmware real (inclusive o próprio dispatcher de diagnóstico e pelo menos uma
  rotina de habilitação de canal/CAN) são chamados só por tabela+`CALLI` em
  runtime, não por `CALLS`/`CALLA` literal — buscas estáticas por chamador não
  acham nada. Resolver isso precisaria de rastreamento de fluxo em runtime, não
  só análise estática.
- **`0xFF32` (PWMCON1) e `0xFE88` (CC4) ficam em `0x0000`** (reset real
  documentado), mas o firmware real claramente espera outro valor ali em algum
  momento (existe código de configuração real pra `0xFF32` no binário) — a
  inicialização real não foi encontrada (ver item de despacho indireto acima).
- **`JMPR`/`CALLR`/`PUSH`/`POP` fazem aritmética de 16 bits só no `IP`, sem
  cruzar segmento** — limitação aceita (hardware real também não cruza; nossa
  implementação só simplifica tratando `pc` como plano em vez de `CSP`+`IP`
  separados, mas o efeito observável é o mesmo dentro de um segmento).
- **`bitbit`/formas indiretas mais raras** (`BAND`/`BOR`/`BXOR`/`BCMP` — ordem
  dos operandos deduzida só do manual, não confirmada contra firmware real como
  as outras correções desta sessão).
- Numa versão anterior do mock (menos fiel, chegava a 1,4M+ instruções), o `pc`
  acabava pousando em flash não-programada (`0xFF` repetido, decodifica como
  `BSET` inofensivo) mais adiante — sinal de que a simulação divergia do hardware
  real em algum ponto anterior (provavelmente efeito cumulativo de mockar
  `ONES`/`PWMCON1`/`CC4` com um contador incremental em vez dos valores reais
  documentados). Não confirmado se a versão atual (mais fiel, mas para mais cedo em
  `0xFEAA`) ainda teria esse problema mais à frente — não chegou lá.
