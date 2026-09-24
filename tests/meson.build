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
  ]
  foreach c : sim_cases
    test('sim-' + c[0],
      find_program('sim_validate.sh'),
      args : [c167cc_exe.full_path(), meson.source_root() / c[1], c[0]] + c[2] + ['--'] + c[3],
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
