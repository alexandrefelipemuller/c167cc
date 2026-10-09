golden_examples = [
  'fatorial',
  'fatorial_rec',
  'bubble_sort',
  'max_vetor',
  'add',
  'calculate',
  'mul_add',
  'abs_ram_global',
  'enum_basic',
  'struct_basic',
  'union_basic',
  'funcptr_basic',
  'farread_global',
  'call_liveness',
  'pointer_arith_scaling_array',
  'cast_qualifier_fixed_addr',
  # BUG-1 (24/09/2026): @ram escalar indexado (uint8_t: *1 + MOVB; uint16_t: *2 + MOV).
  'index_scalar_ram',
  # BUG-2 (24/09/2026, investigado - NÃO era bug do compilador): ||/&& de
  # 3+ termos, mistura, associações explícitas, atribuição e while.
  'logic_chain3',
  # BUG-13 da Sirius32 (01/10/2026): layout de struct/union com membro de 1
  # byte (bytes empacotam, word alinha em par) - `.`/`->`, local, global,
  # @ram, aninhada, array de struct, union, inicializador de global e cópia.
  'struct_byte_layout',
  # BUG-14 da Sirius32 (01/10/2026): `do { } while ();` com break/continue
  # e aninhado com while/for.
  'do_while',
  # BUG-9/BUG-16 da Sirius32 (01/10/2026): `(int8_t)` de `uint8_t` estende o
  # sinal; `>>` com sinal é ASHR e sem sinal continua SHR.
  'sign_cast_shift',
  # BUG-6/7/8/10/11/15 da Sirius32 (01/10/2026): casos mínimos de expressão
  # uint32_t que era rebaixada pra 16 bits (ADDC/SUBC, palavra alta gravada,
  # constante de 32 bits, ternário, comparação das duas palavras, DIVLU).
  'wide32_bugs',
]

foreach name : golden_examples
  test('golden-' + name,
    find_program('golden_test.sh'),
    # The source path is passed relative to workdir (not
    # meson.source_root() / ...) so it ends up verbatim in the generated
    # "; source: ..." comments - keeping the golden .asm files portable
    # across machines/checkout locations instead of baking in an
    # absolute path.
    args : [c167cc_exe.full_path(), 'examples' / (name + '.c'),
            meson.source_root() / 'tests' / 'golden' / (name + '.asm')],
    workdir : meson.source_root())
endforeach

test('determinism',
  find_program('determinism_test.sh'),
  args : [c167cc_exe.full_path(), meson.source_root() / 'examples' / 'calculate.c'])

# Cross-validation against the independent assembler+simulator in
# simulador/ (real C166/C167 opcode encoding, validated against actual
# ECU firmware). Skipped automatically if that directory isn't present.
# (simulador/ moved from the sibling siemens_sir32/ checkout into this repo
# on 02/09/2026 - see tests/sim_validate.sh; this path was left stale
# pointing one level too far up and always resolved to a nonexistent
# directory, but the check was never re-evaluated because `meson.build`
# hadn't been reconfigured since - found and fixed 03/09/2026 alongside the
# IR_DIV32_SYM work.)
sim_dir = meson.source_root() / 'simulador'
if run_command('test', '-d', sim_dir, check : false).returncode() == 0
  # farread_global (IR_FARREAD16_SYM, added 04/09/2026) is NOT in sim_cases
  # below: tests/port_to_toy_asm.py's port() only keeps the function BODY,
  # dropping the `.bss` section - so an `@ram(addr)` symbol's EQU never
  # reaches the toy assembler and the literal far-read offset in the C
  # source has no live connection to wherever c166asm.py's auto-declared
  # flat variable actually lands. The golden-farread_global test above
  # still guards the emitted EXTP/MOV instruction sequence; the feature's
  # real cross-validation against actual firmware (file 0x3B488, sibling
  # Sirius32 project) was done with a dedicated one-off harness instead
  # (see core/aritmetica/biblioteca_aritmetica_enderecos.c there).
  sim_cases = [
    ['fatorial_global', 'examples/fatorial_global.c', ['NUMERO=5'], ['RESULTADO=120']],
    ['calculate_global', 'examples/calculate_global.c', ['RPM=3500', 'LOAD=7'], ['OUT=17']],
    ['mul_add_global', 'examples/mul_add_global.c', ['X=6'], ['OUT=65']],
    ['div32_global', 'examples/div32_global.c', ['A=1234', 'B=777'], ['QUOC=9588', 'REM=18']],
    # Achado 09/09/2026 (6º bug desta sessão, ver docs/limitations.md/
    # "Fixed bugs"): compor um `uint32_t` a partir de duas metades de 16
    # bits, `((uint32_t)hi << 16) | lo`, caía no caminho genérico de 16
    # bits (SHL de 16 bits por 16 posições zera o registrador inteiro, e
    # o OR/store só escrevia a palavra baixa do destino de 32 bits) -
    # HI=0x1234 nunca aparecia em lugar nenhum do resultado. Consertado
    # com IR_COMPOSE32_STORE_SYM, contraparte de IR_SHR32_SYM na direção
    # oposta. HI=0x1234, LO=0x2222 -> OUT_HI=0x1234 (4660), OUT_LO=0x2222
    # (8738); com o bug, OUT_HI seria 0 (ou lixo) e OUT_LO ficaria certo
    # por coincidência (o OR com o SHL zerado é um no-op).
    ['compose32_global', 'examples/compose32_global.c', ['HI=4660', 'LO=8738'], ['OUT_HI=4660', 'OUT_LO=8738']],
    ['div32_mul_global', 'examples/div32_mul_global.c', ['A=1234', 'B=777'], ['QUOC=9588', 'REM=18']],
    ['signext_byte_global', 'examples/signext_byte_global.c', ['IN=200'], ['OUT=-56']],
    ['sign_param_signed_compare_global', 'examples/sign_param_signed_compare_global.c', ['IN_S8=140'], ['OUT=1']],
    # Achado 07/09/2026 (4º bug de cast desta sessão, ver docs/limitations.md/
    # "Fixed bugs"): estreitamento (uint16_t->uint8_t) usado DENTRO de uma
    # expressão maior (não como store final direto) perdia o truncamento por
    # completo - `A + B - (uint8_t)B` virava `A + B - B` porque o otimizador
    # tratava o cast como cópia pura e o codegen não emitia AND #0x00FF/
    # sign-extend algum pro caso de destino de 1 byte. B=511 (> 0xFF) só passa
    # se o truncamento de fato acontecer: (uint8_t)511 == 255, então
    # 10 + 511 - 255 == 266; com o bug dava 10 + 511 - 511 == 10.
    ['narrowing_cast_in_expr_global', 'examples/narrowing_cast_in_expr_global.c', ['A=10', 'B=511'], ['OUT=266']],
    # Achado 07/09/2026 (5º bug desta sessão, ver docs/limitations.md/
    # "Fixed bugs"): `ptr + literal`/`&x + literal` atribuído a uma variável
    # (fora de um `array[i]` com colchetes, que já escalava certo) somava o
    # literal cru em BYTES em vez de escalar por sizeof(*ptr). `&A + 3` num
    # `uint16_t*` deve avançar 6 bytes, não 3 - `off = (uint16_t)p - base`
    # torna a diferença observável sem depender de outra variável global
    # estar alocada logo em seguida (o harness "toy" só declara símbolos que
    # aparecem no corpo portado da função, não os globais do `.bss`
    # originais - ver tests/port_to_toy_asm.py). Com o bug, OUT=3; corrigido,
    # OUT=6.
    ['pointer_arith_scaling_global', 'examples/pointer_arith_scaling_global.c', ['A=100'], ['OUT=6']],
    # Mesmo padrão com ponteiro de 1 byte (uint8_t*): sizeof(*ptr)==1, então
    # escalar é um no-op - este caso já funcionava antes do fix (por sorte,
    # não por tratamento especial) e serve de guarda de regressão pra
    # garantir que o fix acima (que só evita escalar quando esz==1) não
    # quebrou esse caminho por acidente.
    ['pointer_arith_scaling_byte_global', 'examples/pointer_arith_scaling_byte_global.c', ['A=5'], ['OUT=3']],
    # Achado 19/09/2026: `(volatile uint16_t *)ADDR`/`(const uint16_t *)ADDR`
    # eram rejeitados pelo parser ("syntax error (near 'volatile')") - o
    # qualificador só era aceito em declaração, não dentro de um cast de
    # expressão (ver docs/limitations.md). Exercita escrita+leitura de
    # verdade por esse cast, não só o parse (isso já é coberto pelo golden
    # golden-cast_qualifier_fixed_addr acima).
    ['cast_qualifier_sfr_global', 'examples/cast_qualifier_sfr_global.c', ['IN=1234'], ['OUT=1234']],
    # BUG-3 da Sirius32 (24/09/2026, ver docs/limitations.md): NEG/CPL agora
    # montados na codificação real "81 n0"/"91 n0" - garante que o caminho
    # c167cc -> c166asm -> c166sim de `-x`/`~x` continua coerente.
    ['neg_cpl_global', 'examples/neg_cpl_global.c', ['A=4660'], ['OUT_NEG=-4660', 'OUT_CPL=-4661']],
    # BUG-4 da Sirius32 (24/09/2026, ver docs/limitations.md): DIV/DIVU/DIVL/
    # DIVLU agora montados na codificação real "xB nn". `/` e `%` em
    # uint16_t (50000/7: só passa se for DIVU de verdade - o sinal de 0xC350
    # daria -2219), int16_t com dividendo e com divisor negativos
    # (truncamento pra zero, resto com o sinal do dividendo) e uint32_t/
    # uint16_t com MDH != 0 (1000000/300 = 3333 resto 100). A saída do
    # simulador imprime tudo como int16.
    ['div_mod_global', 'examples/div_mod_global.c',
     ['UA=50000', 'UB=7', 'SA=-1000', 'SB=7', 'LHI=15', 'LLO=16960', 'LB=300'],
     ['OUT_UQ=7142', 'OUT_UR=6', 'OUT_SQ=-142', 'OUT_SR=-6', 'OUT_LQ=3333', 'OUT_LR=100']],
  ]
  sim_cases += [
    # BUG-13 da Sirius32 (01/10/2026, ver docs/limitations.md): membro
    # uint8_t de struct era alinhado em palavra (`b` lido em +2, `c` em +4).
    # A struct {u8 a; u8 b; u16 c; u8 d; u16 e;} é sobreposta por ponteiro a
    # BUF = {0x2211, IN, 0x6655, 0x0888}: a=0x11, b=0x22, c=IN, d=0x55,
    # e=0x0888 (+5 é padding); depois grava b=0x7F e d=0x01 e relê as words
    # (0x7F11 = 32529, 0x6601 = 26113) - prova que a escrita é MOVB no byte
    # certo e não mexe no vizinho.
    ['struct_byte_layout_global', 'examples/struct_byte_layout_global.c', ['IN=17203'],
     ['OUT_A=17', 'OUT_B=34', 'OUT_C=17203', 'OUT_D=85', 'OUT_E=2184', 'OUT_W0=32529', 'OUT_W2=26113']],
    # BUG-14 da Sirius32 (01/10/2026, ver docs/limitations.md): do/while.
    # N=5 tem 3 bits; soma dos ímpares 1..LIM=6 é 9 (continue vai pro teste);
    # aninhado com while/for dá 18.
    ['do_while_global', 'examples/do_while_global.c', ['N=5', 'LIM=6'],
     ['OUT_BITS=3', 'OUT_ONCE=1', 'OUT_SUM=9', 'OUT_NEST=18']],
  ]
  # BUG-9 da Sirius32 (01/10/2026): `(int8_t)` de `uint8_t` (e vizinhos:
  # `(int16_t)(int8_t)u8`, `(int8_t)` de uint16_t, `(uint8_t)` de int16_t/
  # int8_t negativos, local int8_t lida depois). Mesma função com entradas
  # negativas e positivas, por isso fora de sim_cases (lá o nome do teste é
  # também o label da função). D=200 é -56 como int8_t; W=0x1280 -> -128;
  # S8=-116 -> 140. Com o bug: OUT_GT=1, OUT_LT=0, OUT_U8S8=-116.
  sign_cast_cases = [
    ['neg', ['D=200', 'W=4736', 'S16=-2', 'S8=-116'],
     ['OUT_GT=0', 'OUT_LT=1', 'OUT_WIDE=-56', 'OUT_FROM_W=-128', 'OUT_U8=254', 'OUT_U8S8=140', 'OUT_LOC=-56', 'OUT_LOC_NEG=1']],
    ['pos', ['D=100', 'W=4657', 'S16=300', 'S8=5'],
     ['OUT_GT=1', 'OUT_LT=0', 'OUT_WIDE=100', 'OUT_FROM_W=49', 'OUT_U8=44', 'OUT_U8S8=5', 'OUT_LOC=100', 'OUT_LOC_NEG=0']],
  ]
  foreach c : sign_cast_cases
    test('sim-sign_cast_global_' + c[0],
      find_program('sim_validate.sh'),
      args : [c167cc_exe.full_path(), meson.source_root() / 'examples' / 'sign_cast_global.c',
              'sign_cast_global'] + c[1] + ['--'] + c[2],
      workdir : meson.source_root())
  endforeach

  # BUG-16 da Sirius32 (01/10/2026): `>>` com sinal é ASHR (int16_t global e
  # local, int8_t, contagem constante e variável, constante dobrada) e sem
  # sinal continua SHR (U=64536 >> 6 = 1008, não -16). Com o bug, S=-1000
  # dava OUT_C=1008, OUT_V=16134, OUT_B=32718, OUT_FOLD=2048.
  ashr_cases = [
    ['neg', ['S=-1000', 'B=-100', 'U=64536', 'UB=200', 'N=2'],
     ['OUT_C=-16', 'OUT_V=-250', 'OUT_LOC=-125', 'OUT_ASSIGN=-250', 'OUT_B=-50', 'OUT_B2=-13', 'OUT_FOLD=-2048', 'OUT_U=1008', 'OUT_UV=16134', 'OUT_UB=100']],
    ['pos', ['S=1000', 'B=100', 'U=1000', 'UB=100', 'N=3'],
     ['OUT_C=15', 'OUT_V=125', 'OUT_LOC=125', 'OUT_ASSIGN=125', 'OUT_B=50', 'OUT_B2=6', 'OUT_FOLD=-2048', 'OUT_U=15', 'OUT_UV=125', 'OUT_UB=50']],
  ]
  foreach c : ashr_cases
    test('sim-ashr_global_' + c[0],
      find_program('sim_validate.sh'),
      args : [c167cc_exe.full_path(), meson.source_root() / 'examples' / 'ashr_global.c',
              'ashr_global'] + c[1] + ['--'] + c[2],
      workdir : meson.source_root())
  endforeach

  # BUG-17 da Sirius32 (02/10/2026): cast no inicializador não troca mais o
  # tipo da variável declarada (nem o dos declaradores seguintes), e
  # `int32_t >> n` usa ASHR na palavra alta. n = 0x8765F234: n >> 4 =
  # 0xF8765F23. W = 0x1234: w = 0x34 + 0x300; b = 0x34; second = 0x1234.
  # Com o bug, OUT_HI=0 (n era uint16_t), OUT_WORD=52 e OUT_SECOND=104.
  test('sim-decl_cast_init_global',
    find_program('sim_validate.sh'),
    args : [c167cc_exe.full_path(), meson.source_root() / 'examples' / 'decl_cast_init_global.c',
            'decl_cast_init_global', 'LO=-3532', 'HI=-30875', 'W=4660', '--',
            'OUT_HI=-30875', 'OUT_LO=-3532', 'OUT_ASHR_HI=-1930', 'OUT_ASHR_LO=24355',
            'OUT_WORD=820', 'OUT_BYTE=52', 'OUT_SECOND=4712'],
    workdir : meson.source_root())

  foreach c : sim_cases
    test('sim-' + c[0],
      find_program('sim_validate.sh'),
      args : [c167cc_exe.full_path(), meson.source_root() / c[1], c[0]] + c[2] + ['--'] + c[3],
      workdir : meson.source_root())
  endforeach

  # BUG-3 da Sirius32 (24/09/2026, ver docs/limitations.md): NEG/CPL/NEGB/
  # CPLB decodificados com o registrador no nibble ALTO ("n0", manual
  # Infineon), em R0/R15/RL0/RH7 etc., flags, e os bytes reais do firmware
  # ("91 D0" = CPL R13, "81 40 18 50 81 50" = negação de 32 bits).
  test('sim-cpl_neg_encoding',
    find_program('python3'),
    args : [meson.source_root() / 'tests' / 'sim_cpl_neg_test.py', sim_dir],
    workdir : meson.source_root())

  # BUG-4 da Sirius32 (24/09/2026, ver docs/limitations.md): DIV/DIVU/
  # DIVL/DIVLU com o divisor repetido nos dois nibbles ("xB nn", manual
  # Infineon), em R0/R5/R15, quociente/resto, sinal, overflow, divisor 0,
  # e os bytes reais do firmware ("5B 55" = DIVU R5, "4B 22" = DIV R2).
  # Mesma função de sim-div_mod_global, outro conjunto de entradas (fica
  # fora de sim_cases porque lá o nome do teste é também o label da função):
  # divisor int16_t negativo, uint16_t 0xFFFF/256 e dividendo 32 bits < divisor.
  test('sim-div_mod_global_neg_divisor',
    find_program('sim_validate.sh'),
    args : [c167cc_exe.full_path(), meson.source_root() / 'examples' / 'div_mod_global.c',
            'div_mod_global', 'UA=65535', 'UB=256', 'SA=1000', 'SB=-7', 'LHI=0', 'LLO=299', 'LB=300',
            '--', 'OUT_UQ=255', 'OUT_UR=255', 'OUT_SQ=-142', 'OUT_SR=6', 'OUT_LQ=0', 'OUT_LR=299'],
    workdir : meson.source_root())

  test('sim-div_encoding',
    find_program('python3'),
    args : [meson.source_root() / 'tests' / 'sim_div_test.py', sim_dir],
    workdir : meson.source_root())

  # BUG-5 da Sirius32 (24/09/2026, ver docs/limitations.md): MDH=0xFE0C e
  # MDL=0xFE0E (estavam trocados). Lê MD sempre por ENDEREÇO (ou pelo campo
  # reg 06h/07h) após MUL/MULU/DIV/DIVU/DIVL/DIVLU, e roda bytes reais do
  # firmware que acessam MD por endereço (file 0x1682, 0x16A2, 0x17AC).
  test('sim-md_addr',
    find_program('python3'),
    args : [meson.source_root() / 'tests' / 'sim_md_addr_test.py', sim_dir],
    workdir : meson.source_root())

  # BUG-12 da Sirius32 (01/10/2026): Z de ADDC/ADDCB/SUBC/SUBCB é encadeado
  # com o Z anterior (manual: "Set if result equals zero and the previous Z
  # flag was set"), e C/V levam o carry de entrada em conta. Cobre todos os
  # modos de endereçamento (word e byte), comparação de 32 bits `SUB lo ;
  # SUBC hi ; JMPR cc` e os bytes reais do firmware (file 0x1534).
  test('sim-addc_subc_z',
    find_program('python3'),
    args : [meson.source_root() / 'tests' / 'sim_addc_subc_z_test.py', sim_dir],
    timeout : 120,
    workdir : meson.source_root())

  # BUG-2 da Sirius32 (investigado 24/09/2026, ver docs/limitations.md):
  # suspeita de que `A || B || C` não testava C quando A e B eram falsos.
  # Não se confirmou; estes 8 casos (todas as combinações V/F de A/B/C,
  # o sufixo do nome do teste são os bits A/B/C) provam em execução
  # ||/&& de 3 termos, `A || B && C`, `(A || B) || C` e `A || (B || C)`.
  # Mesma função pra todos os casos, então fica fora de sim_cases (lá o
  # nome do teste é também o label da função).
  # BUG-14 da Sirius32: mesma função de sim-do_while_global, outras entradas
  # (fora de sim_cases porque lá o nome do teste é também o label da função).
  # N=0: o corpo roda uma vez mesmo com a condição já falsa (OUT_BITS=1);
  # LIM=100: o `break` em k>9 encerra o laço (1+3+5+7+9 = 25). N=65535: 16
  # voltas; LIM=1: condição falsa na 1ª volta (soma 1).
  do_while_cases = [
    ['zero_break', ['N=0', 'LIM=100'], ['OUT_BITS=1', 'OUT_ONCE=1', 'OUT_SUM=25', 'OUT_NEST=18']],
    ['max_once', ['N=65535', 'LIM=1'], ['OUT_BITS=16', 'OUT_ONCE=1', 'OUT_SUM=1', 'OUT_NEST=18']],
  ]
  foreach c : do_while_cases
    test('sim-do_while_global_' + c[0],
      find_program('sim_validate.sh'),
      args : [c167cc_exe.full_path(), meson.source_root() / 'examples' / 'do_while_global.c',
              'do_while_global'] + c[1] + ['--'] + c[2],
      workdir : meson.source_root())
  endforeach

  logic_chain3_cases = [
    ['000', ['A=0', 'B=0', 'C=0'], ['OUT_OR=0', 'OUT_AND=0', 'OUT_MIX=0', 'OUT_ORL=0', 'OUT_ORR=0']],
    ['001', ['A=0', 'B=0', 'C=48'], ['OUT_OR=1', 'OUT_AND=0', 'OUT_MIX=0', 'OUT_ORL=1', 'OUT_ORR=1']],
    ['010', ['A=0', 'B=32', 'C=0'], ['OUT_OR=1', 'OUT_AND=0', 'OUT_MIX=0', 'OUT_ORL=1', 'OUT_ORR=1']],
    ['011', ['A=0', 'B=32', 'C=48'], ['OUT_OR=1', 'OUT_AND=0', 'OUT_MIX=1', 'OUT_ORL=1', 'OUT_ORR=1']],
    ['100', ['A=16', 'B=0', 'C=0'], ['OUT_OR=1', 'OUT_AND=0', 'OUT_MIX=1', 'OUT_ORL=1', 'OUT_ORR=1']],
    ['101', ['A=16', 'B=0', 'C=48'], ['OUT_OR=1', 'OUT_AND=0', 'OUT_MIX=1', 'OUT_ORL=1', 'OUT_ORR=1']],
    ['110', ['A=16', 'B=32', 'C=0'], ['OUT_OR=1', 'OUT_AND=0', 'OUT_MIX=1', 'OUT_ORL=1', 'OUT_ORR=1']],
    ['111', ['A=16', 'B=32', 'C=48'], ['OUT_OR=1', 'OUT_AND=1', 'OUT_MIX=1', 'OUT_ORL=1', 'OUT_ORR=1']],
  ]
  foreach c : logic_chain3_cases
    test('sim-logic_chain3_global_' + c[0],
      find_program('sim_validate.sh'),
      args : [c167cc_exe.full_path(), meson.source_root() / 'examples' / 'logic_chain3_global.c',
              'logic_chain3_global'] + c[1] + ['--'] + c[2],
      workdir : meson.source_root())
  endforeach

  # ---- 32 bits como par de palavras (BUG-6/7/8/10/11/15 da Sirius32) ----
  # Mesma função pra vários conjuntos de entrada, por isso fora de sim_cases
  # (lá o nome do teste é também o label da função). Valores esperados
  # calculados em Python com aritmética de 32 bits; a saída do simulador é
  # impressa como int16.
  # BUG-8/BUG-10/BUG-11 da Sirius32 (01/10/2026): +, -, negação, ternário,
  # constante, ++/+=, &|^ e deslocamento de uint32_t com as DUAS palavras
  # (ADD/ADDC, SUB/SUBC). Casos: vai-um da palavra baixa pra alta
  # (0x0001FFFF + 0x00020001), sem vai-um, empréstimo atravessando
  # (0x80000000 - 0x7FFFFFFF), 0 - 1 e 0xFFFFFFFF + 1 (estouro de 32 bits).
  wide32_arith_global_cases = [
    ['0',
     ['AHI=1', 'ALO=65535', 'BHI=2', 'BLO=1', 'X=65535', 'NEG=1'],
     ['SUM_HI=4', 'SUM_LO=0', 'DIF_HI=-1', 'DIF_LO=-2', 'ADX_HI=2', 'ADX_LO=-2', 'TER_HI=-2', 'TER_LO=1', 'K_HI=4', 'K_LO=-12145', 'INC_HI=3', 'INC_LO=0', 'BIT_HI=254', 'BIT_LO=-255', 'SHL_HI=31', 'SHL_LO=-16', 'SHR_HI=0', 'SHR_LO=16384']],
    ['1',
     ['AHI=4660', 'ALO=1', 'BHI=564', 'BLO=2', 'X=5', 'NEG=0'],
     ['SUM_HI=5224', 'SUM_LO=3', 'DIF_HI=4095', 'DIF_LO=-1', 'ADX_HI=4660', 'ADX_LO=6', 'TER_HI=4660', 'TER_LO=1', 'K_HI=3', 'K_LO=-12139', 'INC_HI=4661', 'INC_LO=2', 'BIT_HI=4863', 'BIT_LO=254', 'SHL_HI=9024', 'SHL_LO=16', 'SHR_HI=326', 'SHR_LO=-32768']],
    ['2',
     ['AHI=32768', 'ALO=0', 'BHI=32767', 'BLO=65535', 'X=1', 'NEG=1'],
     ['SUM_HI=-1', 'SUM_LO=-1', 'DIF_HI=0', 'DIF_LO=1', 'ADX_HI=-32768', 'ADX_LO=1', 'TER_HI=-32768', 'TER_LO=0', 'K_HI=3', 'K_LO=-12143', 'INC_HI=-32767', 'INC_LO=1', 'BIT_HI=-32513', 'BIT_LO=255', 'SHL_HI=0', 'SHL_LO=0', 'SHR_HI=4095', 'SHR_LO=-1']],
    ['3',
     ['AHI=0', 'ALO=0', 'BHI=0', 'BLO=1', 'X=0', 'NEG=1'],
     ['SUM_HI=0', 'SUM_LO=1', 'DIF_HI=-1', 'DIF_LO=-1', 'ADX_HI=0', 'ADX_LO=0', 'TER_HI=0', 'TER_LO=0', 'K_HI=3', 'K_LO=-12144', 'INC_HI=1', 'INC_LO=1', 'BIT_HI=255', 'BIT_LO=255', 'SHL_HI=0', 'SHL_LO=0', 'SHR_HI=0', 'SHR_LO=0']],
    ['4',
     ['AHI=65535', 'ALO=65535', 'BHI=0', 'BLO=1', 'X=65535', 'NEG=0'],
     ['SUM_HI=0', 'SUM_LO=0', 'DIF_HI=-1', 'DIF_LO=-2', 'ADX_HI=0', 'ADX_LO=-2', 'TER_HI=-1', 'TER_LO=-1', 'K_HI=4', 'K_LO=-12145', 'INC_HI=1', 'INC_LO=0', 'BIT_HI=-256', 'BIT_LO=-255', 'SHL_HI=-1', 'SHL_LO=-16', 'SHR_HI=0', 'SHR_LO=0']],
  ]
  foreach c : wide32_arith_global_cases
    test('sim-wide32_arith_global_' + c[0],
      find_program('sim_validate.sh'),
      args : [c167cc_exe.full_path(), meson.source_root() / 'examples' / 'wide32_arith_global.c',
              'wide32_arith_global'] + c[1] + ['--'] + c[2],
      workdir : meson.source_root())
  endforeach

  # BUG-11 da Sirius32: <, >, <=, >=, ==, != de uint32_t/int32_t comparando
  # as duas palavras. Casos: palavra alta maior com a baixa MENOR (e o
  # inverso - a comparação só da palavra baixa dá o contrário), iguais,
  # altas diferentes/baixas iguais, altas iguais/baixas diferentes, sinais
  # diferentes (int32_t negativo < positivo, que sem sinal é o contrário) e
  # baixas que só diferem no bit 15. ==/!= são dois CMP de palavra (cc_NZ):
  # NÃO dependem do flag Z encadeado de SUBC (BUG-12 da Sirius32, no
  # simulador). </>= usam C (sem sinal) ou N/V (com sinal) depois de
  # CMP+SUBC; o caso 0x80000000 vs 0x7FFFFFFF com sinal fica de fora porque
  # o c166sim.py calcula o V do SUBC a partir de (op2 + carry), errado quando
  # op2 = 0x7FFF e há empréstimo (o código gerado está certo pelo manual).
  wide32_cmp_global_cases = [
    ['0',
     ['AHI=1', 'ALO=0', 'BHI=0', 'BLO=65535'],
     ['OUT_U=42', 'OUT_S=42', 'OUT_K=26', 'OUT_C=5']],
    ['1',
     ['AHI=0', 'ALO=65535', 'BHI=1', 'BLO=0'],
     ['OUT_U=37', 'OUT_S=37', 'OUT_K=9', 'OUT_C=5']],
    ['2',
     ['AHI=5', 'ALO=7', 'BHI=5', 'BLO=7'],
     ['OUT_U=28', 'OUT_S=28', 'OUT_K=10', 'OUT_C=4']],
    ['3',
     ['AHI=5', 'ALO=7', 'BHI=6', 'BLO=7'],
     ['OUT_U=37', 'OUT_S=37', 'OUT_K=10', 'OUT_C=5']],
    ['4',
     ['AHI=6', 'ALO=7', 'BHI=5', 'BLO=7'],
     ['OUT_U=42', 'OUT_S=42', 'OUT_K=10', 'OUT_C=5']],
    ['5',
     ['AHI=5', 'ALO=7', 'BHI=5', 'BLO=8'],
     ['OUT_U=37', 'OUT_S=37', 'OUT_K=10', 'OUT_C=5']],
    ['6',
     ['AHI=5', 'ALO=9', 'BHI=5', 'BLO=8'],
     ['OUT_U=42', 'OUT_S=42', 'OUT_K=10', 'OUT_C=5']],
    ['7',
     ['AHI=65535', 'ALO=65534', 'BHI=0', 'BLO=1'],
     ['OUT_U=42', 'OUT_S=37', 'OUT_K=2', 'OUT_C=5']],
    ['8',
     ['AHI=0', 'ALO=1', 'BHI=65535', 'BLO=65534'],
     ['OUT_U=37', 'OUT_S=42', 'OUT_K=1', 'OUT_C=5']],
    ['9',
     ['AHI=32768', 'ALO=1', 'BHI=32767', 'BLO=0'],
     ['OUT_U=42', 'OUT_S=37', 'OUT_K=6', 'OUT_C=5']],
    ['10',
     ['AHI=32767', 'ALO=0', 'BHI=32768', 'BLO=1'],
     ['OUT_U=37', 'OUT_S=42', 'OUT_K=10', 'OUT_C=5']],
    ['11',
     ['AHI=0', 'ALO=0', 'BHI=0', 'BLO=0'],
     ['OUT_U=28', 'OUT_S=28', 'OUT_K=1', 'OUT_C=2']],
    ['12',
     ['AHI=65535', 'ALO=32767', 'BHI=65535', 'BLO=32768'],
     ['OUT_U=37', 'OUT_S=37', 'OUT_K=6', 'OUT_C=5']],
    ['13',
     ['AHI=0', 'ALO=32768', 'BHI=0', 'BLO=32767'],
     ['OUT_U=42', 'OUT_S=42', 'OUT_K=9', 'OUT_C=5']],
  ]
  foreach c : wide32_cmp_global_cases
    test('sim-wide32_cmp_global_' + c[0],
      find_program('sim_validate.sh'),
      args : [c167cc_exe.full_path(), meson.source_root() / 'examples' / 'wide32_cmp_global.c',
              'wide32_cmp_global'] + c[1] + ['--'] + c[2],
      workdir : meson.source_root())
  endforeach

  # BUG-6/BUG-7/BUG-15 da Sirius32: x + a*b com ADDC do MDH (inclui vai-um
  # da soma das palavras baixas e operandos >= 0x8000, MULU de verdade),
  # quociente uint32_t = n / d com a palavra alta gravada (= 0; a variável
  # começa em 0xFFFFFFFF), ((uint32_t)hi << 16) | lo direto como dividendo
  # de / e % (MDH:MDL + DIVLU), 32x16 com produto cruzado e (n + x) / d.
  wide32_muldiv_global_cases = [
    ['0',
     ['X=65535', 'MA=4660', 'MB=256', 'NHI=18', 'NLO=13398', 'D=4096'],
     ['B6_HI=19', 'B6_LO=13311', 'B7_HI=0', 'B7_LO=291', 'B15_Q=291', 'B15_R=1110', 'MW_HI=19296', 'MW_LO=-21128', 'DM_Q=307']],
    ['1',
     ['X=32768', 'MA=65535', 'MB=65535', 'NHI=1', 'NLO=0', 'D=2'],
     ['B6_HI=-2', 'B6_LO=-32767', 'B7_HI=0', 'B7_LO=-32768', 'B15_Q=-32768', 'B15_R=0', 'MW_HI=-1', 'MW_LO=0', 'DM_Q=-16384']],
    ['2',
     ['X=1', 'MA=2', 'MB=3', 'NHI=0', 'NLO=100', 'D=7'],
     ['B6_HI=0', 'B6_LO=7', 'B7_HI=0', 'B7_LO=14', 'B15_Q=14', 'B15_R=2', 'MW_HI=0', 'MW_LO=200', 'DM_Q=14']],
    ['3',
     ['X=61440', 'MA=49152', 'MB=32772', 'NHI=255', 'NLO=65535', 'D=32767'],
     ['B6_HI=24579', 'B6_LO=-4096', 'B7_HI=0', 'B7_LO=512', 'B15_Q=512', 'B15_R=511', 'MW_HI=-1', 'MW_LO=16384', 'DM_Q=513']],
  ]
  foreach c : wide32_muldiv_global_cases
    test('sim-wide32_muldiv_global_' + c[0],
      find_program('sim_validate.sh'),
      args : [c167cc_exe.full_path(), meson.source_root() / 'examples' / 'wide32_muldiv_global.c',
              'wide32_muldiv_global'] + c[1] + ['--'] + c[2],
      workdir : meson.source_root())
  endforeach

  # BUG-11 da Sirius32: uint32_t por ponteiro ([reg] e [reg+2]), int16_t
  # alargado com extensão de sinal (soma/subtração saturada em int32_t, o
  # padrão de core/aritmetica/lote_s_saturada_diversos.c da Sirius32, que
  # antes nunca saturava), produto com sinal (MUL) somado, e uma expressão
  # com muitas palavras vivas (spill no meio de ADD/ADDC).
  wide32_misc_global_cases = [
    ['0',
     ['S1=30000', 'S2=30000', 'U1=65535', 'U2=65535'],
     ['PT_HI=3', 'PT_LO=1', 'SAT_ADD=32767', 'SAT_SUB=0', 'SP_HI=13733', 'SP_LO=24112', 'MIX_HI=0', 'MIX_LO=-9033']],
    ['1',
     ['S1=-30000', 'S2=30000', 'U1=4660', 'U2=32769'],
     ['PT_HI=4663', 'PT_LO=5', 'SAT_ADD=0', 'SAT_SUB=-32768', 'SP_HI=-13734', 'SP_LO=-24112', 'MIX_HI=2329', 'MIX_LO=19080']],
    ['2',
     ['S1=-30000', 'S2=-30000', 'U1=1', 'U2=2'],
     ['PT_HI=3', 'PT_LO=7', 'SAT_ADD=-32768', 'SAT_SUB=0', 'SP_HI=13732', 'SP_LO=29648', 'MIX_HI=-1', 'MIX_LO=-9029']],
    ['3',
     ['S1=100', 'S2=-7', 'U1=32768', 'U2=32767'],
     ['PT_HI=-32765', 'PT_LO=1', 'SAT_ADD=93', 'SAT_SUB=107', 'SP_HI=-1', 'SP_LO=-600', 'MIX_HI=16383', 'MIX_LO=-9030']],
    ['4',
     ['S1=-1', 'S2=-1', 'U1=0', 'U2=65534'],
     ['PT_HI=3', 'PT_LO=-1', 'SAT_ADD=-2', 'SAT_SUB=0', 'SP_HI=0', 'SP_LO=0', 'MIX_HI=-1', 'MIX_LO=-9031']],
  ]
  foreach c : wide32_misc_global_cases
    test('sim-wide32_misc_global_' + c[0],
      find_program('sim_validate.sh'),
      args : [c167cc_exe.full_path(), meson.source_root() / 'examples' / 'wide32_misc_global.c',
              'wide32_misc_global'] + c[1] + ['--'] + c[2],
      workdir : meson.source_root())
  endforeach
  # Regressão em nível de execução (não só texto pinado) pro bug de
  # 06/09/2026 de "valor vivo através de chamada" - ver
  # tests/call_liveness_runtime_test.sh e examples/call_liveness.c.
  # Usa a ABI real (port_real_abi.py), não o harness "toy" acima (que não
  # suporta CALLR), porque o bug só se manifesta com uma chamada de
  # verdade.
  test('golden-call_liveness-runtime',
    find_program('call_liveness_runtime_test.sh'),
    args : [c167cc_exe.full_path()],
    workdir : meson.source_root())
endif

  test('sim-exts_far_access',
    find_program('python3'),
    args : [meson.source_root() / 'tests' / 'sim_exts_test.py', sim_dir],
    workdir : meson.source_root())
