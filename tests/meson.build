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
  sim_cases = [
    ['fatorial_global', 'examples/fatorial_global.c', ['NUMERO=5'], ['RESULTADO=120']],
    ['calculate_global', 'examples/calculate_global.c', ['RPM=3500', 'LOAD=7'], ['OUT=17']],
    ['mul_add_global', 'examples/mul_add_global.c', ['X=6'], ['OUT=65']],
    ['div32_global', 'examples/div32_global.c', ['A=1234', 'B=777'], ['QUOC=9588', 'REM=18']],
  ]
  foreach c : sim_cases
    test('sim-' + c[0],
      find_program('sim_validate.sh'),
      args : [c167cc_exe.full_path(), meson.source_root() / c[1], c[0]] + c[2] + ['--'] + c[3],
      workdir : meson.source_root())
  endforeach
endif
