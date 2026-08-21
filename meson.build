project('c167cc', 'c',
  version : '0.1.0',
  default_options : ['c_std=gnu11', 'warning_level=1'])

flex = find_program('flex')
bison = find_program('bison')

lexer = custom_target('lexer',
  input : 'src/parser/lexer.l',
  output : ['lexer.yy.c'],
  command : [flex, '-o', '@OUTPUT0@', '@INPUT@'])

parser = custom_target('parser',
  input : 'src/parser/parser.y',
  output : ['parser.tab.c', 'parser.tab.h'],
  command : [bison, '-d', '-o', '@OUTPUT0@', '@INPUT@'])

inc = include_directories('include')

sources = [
  lexer, parser,
  'src/ast/ast.c',
  'src/semantic/semantic.c',
  'src/ir/ir_build.c',
  'src/ir/ir_dump.c',
  'src/optimizer/optimizer.c',
  'src/target/c167/instructions/isa.c',
  'src/target/c167/registers/regalloc.c',
  'src/target/c167/codegen/codegen.c',
  'src/target/c167/printer/printer.c',
  'src/driver/main.c',
]

c167cc_exe = executable('c167cc', sources,
  include_directories : inc,
  install : false)

subdir('tests')
