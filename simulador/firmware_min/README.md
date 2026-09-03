# `firmware_min` — firmware próprio (não o real da Copa Clio), compilado de `reimplementacao_c/`

**Última atualização: 21/08/2026.**

⚠️ Este diretório **não é** o firmware real da Copa Clio nem uma tentativa de
reconstruí-lo bit-a-bit. É um experimento separado: um firmware PRÓPRIO,
mínimo, escrito nós mesmos, que fala o mesmo protocolo K-line/KWP2000 que a
central real fala (sessões, SIDs 0x27/0x14/0x23/0x2A/0x29, dispatcher de
código legado, tabela de DTC) — na medida do que já foi reconstruído em
`reimplementacao_c/` — e que dá pra rodar de verdade no simulador e falar com
um app ELM327 real via `bridge_min.py`. Serve pra provar/validar a
reconstrução em C (`reimplementacao_c/`) executando-a de verdade, não só lendo.

## Pergunta direta: "temos um firmware mínimo feito a partir do `.c`?"

**Parcialmente, e a parte que está pronta é 100% real (não simulação de
fachada):**

| Módulo | Origem | Status |
|---|---|---|
| Tabela de DTC (mark/clear/decay/get/is_pending/is_confirmed) | **Compilado** de `reimplementacao_c/dtc/dtc_sirius32.{h,c}` via `c167cc` | ✅ Rodando dentro de `firmware_full.bin`, validado via quadro K-line real (`functional_test.py`) |
| CRC16 (`crc16_sirius32()`, poly 0xA001) | **Compilado** de `reimplementacao_c/checksum/crc16_sirius32.c` via `c167cc` | ✅ Rodando dentro de `firmware_full.bin`, validado contra `../../crc_sirius32.py` sobre bytes reais de `Scenic 2.0 16v.bin` (`crc16_module_test.py`) — mas **sem call site**: nenhum SID/código legado reconstruído chama checksum via K-line, então este módulo fica embutido e testável, só não é invocado por nenhum handler (ver `src/10_crc16.asm`) |
| RX/TX de quadro, sessão, SID 0x27/0x23/0x2A/0x29, OBD legislado, dispatcher legado, scheduler | Hand-written asm (`src/00..07,09,99_*.asm`) | Não compilado — ainda não portado |

Ou seja: **um módulo real do firmware (o mais visado pelo pedido original —
a tabela de DTC) já é genuinamente derivado do `.c`, compilado pelo nosso
próprio `c167cc`**, não transcrito à mão. O resto do `firmware_min` (frame
codec, máquina de sessão, SIDs) continua asm hand-written — portar esses
módulos também é o próximo passo natural (ver `PRIORIDADE DOS MÓDULOS`
abaixo), mas ainda não foi feito. Não afirmar "o firmware inteiro vem do
.c" — isso seria impreciso até os módulos restantes serem portados.

## Pipeline (C → binário rodando)

```
reimplementacao_c/X/*.h + *.c
        │  (edição in-place, mecânica: remove typedef/stdbool/string.h/size_t -
        │   ver "por que reimplementacao_c foi editado" abaixo)
        ▼
port_c_typedef.py --write   (roda sobre o C, ANTES do compilador)
        │
        ▼
concat_c_module.py X.h X.c > /tmp/modulo.c   (c167cc não tem #include)
        │
        ▼
compiler/build/c167cc --dump-asm /tmp/modulo.c > modulo_full.asm
        │
        ▼
port_real_abi.py modulo_full.asm > modulo_ported.asm
        │  (sintático: .section/.global fora, DS N -> RESERVE, MULU/DIVU ->
        │   MUL/DIV, CALLR -> CALLA, JMPR -> JMPA - NUNCA muda semântica)
        ▼
+ trampolins hand-written (ex.: DTC_INIT/DTC_CLEAR/DTC_DECAY_TICK em
  src/08_dtc_table.asm) preservando a interface de registrador que o resto
  do firmware_min já usa (R0=entrada, sem valor de retorno via pilha)
        ▼
build.py  (concatena src/00..99_*.asm em ordem numérica) -> firmware_full.asm
        │
        ▼
simulador/c166asm.py firmware_full.asm firmware_full.bin
        │
        ▼
functional_test.py  (quadro K-line real via bridge_min.KLineTransport)
        │
        ▼
bridge_min.py firmware_full.bin --port 35000   (app ELM327 real via TCP)
```

## Por que `reimplementacao_c/` foi editado in-place (decisão do usuário, 21/08/2026)

`c167cc` não tem pré-processador (`#include`), `typedef`, nem `stdbool.h`/
`string.h`/`size_t` (ver `compiler/docs/limitations.md`). `reimplementacao_c/`
usava esses recursos livremente (é C "de leitura", não pensado pra compilar
antes desta sessão). A correção de rumo explícita foi: **não** criar cópias
paralelas "compiláveis" em outro lugar — os próprios arquivos de
`reimplementacao_c/` evoluem, mecanicamente, pra virarem compiláveis:

- `typedef struct{...} nome_t;` → `struct nome_t{...};` (idem `enum`/`union`)
  — ferramenta: `port_c_typedef.py`.
- `bool`/`true`/`false`/`size_t`/`NULL` → `uint16_t`/`1`/`0`/`uint16_t`/`0`.
- `memcpy`/`memset` → laço `for` explícito (não automatizado — revisado à
  mão, arquivo por arquivo, poucas ocorrências).
- `static`/`extern` removidos (sem unidades de tradução de verdade aqui —
  tudo vira 1 arquivo concatenado por `concat_c_module.py`).

**O comportamento/lógica documentada não muda** — só a mecânica da
declaração de tipo. Os comentários de confiança (✅/🟡/❓/⚫) e a citação de
endereço do binário real em cada função são preservados tal qual.

## Bugs reais de compilador/montador achados PORTANDO módulos de verdade

Portar `dtc_sirius32.c` (o módulo mais complexo já tentado — bitwise
`&=`/`|=`/`^=`, structs, ponteiro de função, retorno por valor, array
indexado em runtime, laços aninhados) expôs bugs reais do `c167cc` e do
`c166asm.py` que nenhum teste sintético pequeno tinha pego. Resumo (detalhe
completo no histórico de commits/sessão, não repetido aqui):

- **Byte-load não zero-estendia** (`MOVB Rn,[...]` só toca o byte baixo do
  registrador de destino na C167 real) — achado 2x, em dois codegens
  diferentes (`IR_LOAD_MEM` e depois `IR_LOAD_SYM`) — corrigido com
  `AND d,#0x00FF` após o `MOVB`.
- **`ADD/SUB SP,#N` usava R6/R7 como scratch** — colidia com os registradores
  de passagem de parâmetro 3º/4º, destruindo parâmetros no próprio prólogo
  da função. Corrigido pra R13/R14.
- **Bounds check faltando em `emit_prologue`** — função com >4 parâmetros
  reais (4 + sret escondido) lia `c167_arg_regs[]` fora dos limites,
  decodificando lixo como registrador válido.
- **`SP` inicializado baixo demais** (achado 2x — uma vez testando o módulo
  isolado, outra integrando no `firmware_full.bin`) — a pilha, crescendo pra
  baixo, sobrescrevia o próprio código/dados assim que o programa cresceu
  além do valor antigo de `SP`. Este é um bug do SCRIPT DE TESTE/do
  `00_header.asm`, não do compilador — mas vale documentar porque é fácil de
  reintroduzir ao portar o PRÓXIMO módulo (maior ainda) sem reconferir o
  valor de `SP`.
- Suporte novo adicionado ao `c167cc`: inicializador de agregado (`= {...}`,
  local e global), retorno de struct por valor (convenção `sret` oculta),
  `&=`/`|=`/`^=`, vírgula sobrando em `enum`.
- Suporte novo adicionado ao `c166asm.py`: `[Rw+#offset]`, `RESERVE`, `DW`,
  `ADD/SUB SP,#N`, `AND/OR/XOR Rw,#imm16`, `MOV Rw,#endereço_absoluto`.
- **Labels locais do `c167cc` não são únicas entre módulos diferentes** (achado
  21/08/2026 portando `crc16_sirius32.c`) — o compilador numera labels
  genéricas de controle de fluxo (`.Lcmp_true_N`, `.Lcmp_end_N`) a partir de 1
  DENTRO de cada unidade de compilação separada; como `build.py` concatena
  todos os fragmentos num único namespace de montador (sem linker de
  verdade), dois módulos compilados independentemente podem gerar o MESMO
  nome de label (`08_dtc_table.asm` e `10_crc16.asm` colidiram em
  `.Lcmp_true_1`/`.Lcmp_end_1`) — o montador aceita a redefinição em silêncio
  e todo `JMPA` pro nome resolve pro ÚLTIMO definido no arquivo, desviando
  execução pro código errado (sintoma: `crc16_sirius32()` saindo do laço na
  1ª iteração, pulando direto pro epílogo, resultado sempre 0). Corrigido à
  mão renomeando as 2 labels colidentes em `10_crc16.asm` com o prefixo do
  próprio módulo. **Não é bug do `c167cc`** (cada `.asm` isolado está correto
  — a numeração só precisa ser única DENTRO da própria unidade compilada,
  que é o contrato que `--dump-asm` cumpre) — é uma responsabilidade do
  PORTE/integração (`port_real_abi.py` ou um passo novo) que ainda não existe:
  nenhuma ferramenta hoje detecta ou previne colisão de labels entre módulos
  compilados separadamente antes da concatenação. Ao portar o próximo módulo
  (`kline_dispatcher.c`), conferir/renomear labels genéricas colidentes antes
  de embutir, ou automatizar isso em `port_real_abi.py` (prefixar todo label
  `.L...` que não já contenha o nome da função com um prefixo único por
  arquivo de origem).

Ver `compiler/docs/limitations.md` e `simulador/README.md` (seção "Bugs
reais achados") pra detalhe linha-a-linha de cada um.

## Fronteira honesta de NRC-stub (não inventar em nenhum módulo)

Mesma política de `reimplementacao_c/`: código nunca decompilado vira NRC
(`Negative Response Code`) explícito, nunca comportamento inventado. Ver
comentários em cada fragmento `src/*.asm` (ex. SID 0x14 sub 0x00 "modo de
calibração especial", SID 0x23 com subfunção != 0x6B/0x6C, SID 0x2A, DTC
índices 10-59) — lista completa no plano do experimento (histórico de
sessão / `notas/PLANO.md` se for promovido pra lá futuramente).

## Arquivos deste diretório

- `src/00_header.asm` .. `99_footer.asm` — fragmentos hand-written
  concatenados em ordem numérica por `build.py`. `08_dtc_table.asm` e
  `10_crc16.asm` são os fragmentos que embutem código **compilado**:
  `08_dtc_table.asm` = trampolins hand-written + corpo gerado por
  `c167cc`+`port_real_abi.py` a partir de
  `reimplementacao_c/dtc/dtc_sirius32.{h,c}`; `10_crc16.asm` = só o corpo
  gerado (sem trampolim — sem call site real conhecido, ver módulo CRC16
  acima) a partir de `reimplementacao_c/checksum/crc16_sirius32.c`.
- `build.py` — concatena `src/*.asm` → `firmware_full.asm` → monta com
  `c166asm.py` → `firmware_full.bin`.
- `port_c_typedef.py` — transforma C fonte (`reimplementacao_c/`, in-place)
  pra compilável (typedef/stdbool/string.h/size_t/NULL/static/extern).
- `concat_c_module.py` — concatena `.h`+`.c` (sem `#include`) num único
  arquivo de compilação.
- `port_real_abi.py` — transforma o `.asm` cru gerado pelo `c167cc`
  (`--dump-asm`) em algo que `c166asm.py` monta, sem mudar semântica.
- `functional_test.py` — teste funcional via quadro K-line REAL (usa
  `bridge_min.KLineTransport`, o mesmo caminho que um app ELM327 real usa) -
  cobre Mode01 PID00, sessão, SID 0x14 handshake/confirm, e prova que o
  confirm realmente aciona o `dtc_sirius32_clear()` COMPILADO (força estado
  real na struct, confere que zera depois do quadro). Regressão permanente
  pra `firmware_full.bin`.
- `bridge_min.py` — ponte TCP↔simulador pro `firmware_full.bin` (ELM327
  real conecta aqui, não em `../obd2_bridge.py`, que é pro firmware REAL).
- `crc16_module_test.py` — regressão do módulo CRC16 compilado: chama
  `crc16_sirius32()` direto pelo endereço (não via K-line — não há call
  site), com registradores da ABI real do `c167cc` (R4/R5/R6), e compara
  contra `../../crc_sirius32.py` sobre bytes reais de `Scenic 2.0 16v.bin`.
- `boot.asm`/`boot.bin`/`smoke_test.py` — referência histórica do Stage 1
  (codec de 6 bytes fixos, formato antigo, ainda passa mas não é mais o
  formato usado por `firmware_full.bin`).

## Próximo módulo (não feito ainda)

`crc16_sirius32.c` (✅ feito, ver tabela acima). Nota de correção de plano: a
ideia original era que ele "trocasse o CRC16 placeholder de
`09_scheduler.asm`" — mas ao chegar nesse módulo confirmou-se que
`09_scheduler.asm` nunca teve um placeholder de CRC16 (a suposição do plano
estava desatualizada); o único checksum hand-written no `firmware_min` é a
soma de 8 bits do CODEC de quadro K-line (`F_CHECKSUM_RXBUF` em
`01_frame_codec.asm`), que é um algoritmo DIFERENTE (não CRC16) e não deve
ser trocado por ele. Por isso o módulo CRC16 ficou compilado e validado, mas
sem nenhum trampolim/call site — não inventar um.

Por prioridade, o que falta: `kline/kline_dispatcher.c`+`obd_legislado.c`
(substituiria `02_session.asm`..`06_sid_2a.asm` inteiros, hoje hand-written).
`scheduler/scheduler_sirius32.c` fica como stretch/último item (maior
arquivo da árvore, inclui handlers de RAMO A fora de escopo).
