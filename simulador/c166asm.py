#!/usr/bin/env python3
"""Montador (assembler) para os .asm de exemplo em siemens_sir32/simulador/.

Gera um .bin com OPCODES REAIS da C166/C167 (mesma tabela usada pelo
desmontador ferramentas_disassembly/c166dis.py, extraída do Infineon C166
Instruction Set Manual - c166ism.pdf). O offset 0 do .bin é o primeiro byte
executado (reset entry point) - não há vetor de reset separado, o código
começa direto ali, como pedido.

IMPORTANTE - os .asm de exemplo (fall.asm, fat.asm, filter.asm) usam uma
sintaxe "pseudo-C166" que a CPU real não aceita literalmente:
  - MOV mem,#imm      (mover imediato direto pra memória)      -> não existe
  - MOV mem,mem        (mover memória pra memória)              -> não existe
  - MUL/DIV com operando de memória (só aceitam GPR real)       -> não existe
  - DEC Rw                                                       -> não existe
Esses casos são expandidos automaticamente em 1-2 instruções reais
equivalentes, usando R6/R7 como registradores de trabalho (scratch)
reservados pelo montador - ver função `expand_line()`. Cada expansão é
documentada no comentário do bloco correspondente. Os arquivos de origem só
usam R0-R3, então R6/R7 estão livres.

Layout de memória gerado:
  0x0000 .. code_end-1      : código (endereços fixos, calculados no passe 1)
  code_end (alinhado a 2)   : variáveis (uma word por padrão; NOME+2 reserva
                              mais - ver `VarTable`)
  MDL = 0xFE0C, MDH = 0xFE0E : endereços padrão Infineon do par
                              multiplicação/divisão (SFR), fora da área de
                              variáveis - checar contra o mapa de SFR do
                              derivado real se for rodar em silício de
                              verdade; para o simulador isso não importa
                              (o mapa de memória é todo nosso).

Uso:
    python3 c166asm.py fall.asm fall.bin
    python3 c166asm.py fat.asm fat.bin
    python3 c166asm.py filter.asm filter.bin
"""
import sys
import re

MDL_ADDR = 0xFE0C
MDH_ADDR = 0xFE0E
SP_ADDR = 0xFE12  # SFR real de Stack Pointer (mesmo endereço usado pelo boot
                  # real da Copa Clio - ver simulador/c166sim.py)
SCRATCH_MEM = 6   # R6: scratch p/ imediato->memória e memória->memória
SCRATCH_MUL = 7   # R7: scratch p/ operandos de memória em MUL/DIV

# Tabela completa de condição (mesma de ferramentas_disassembly/c166dis.py CC{}),
# usada por JMPR/JMPA/CALLA/JMPI/CALLI - todas codificam cc no mesmo nibble/posição.
CC_MAP = {'UC': 0x0, 'NET': 0x1, 'Z': 0x2, 'NZ': 0x3, 'V': 0x4, 'NV': 0x5,
          'N': 0x6, 'NN': 0x7, 'C': 0x8, 'NC': 0x9, 'SGT': 0xA, 'SLE': 0xB,
          'SLT': 0xC, 'SGE': 0xD, 'UGT': 0xE, 'ULE': 0xF}

REG_RE = re.compile(r'^[Rr](\d{1,2})$')
# Registrador de byte RLn/RHn (n=0-7): os 16 GPRs de 16 bits também são endereçáveis
# como 16 registradores de 8 bits, mas empacotados no nibble como (n<<1)|sel (sel=0->L,
# 1->H) - NÃO como "0-7=RL,8-15=RH" (essa era a modelagem antiga, errada, corrigida em
# ferramentas_disassembly/c166dis.py em 19/08/2026 depois de confirmar contra a imagem
# real da Copa Clio via Ghidra).
BREG_RE = re.compile(r'^R([LH])(\d)$')


def is_breg(tok):
    m = BREG_RE.match(tok)
    if m:
        sel = 0 if m.group(1) == 'L' else 1
        regnum = int(m.group(2))
        if 0 <= regnum <= 7:
            return (regnum << 1) | sel
    return None


def is_reg(tok):
    m = REG_RE.match(tok)
    if m:
        n = int(m.group(1))
        if 0 <= n <= 15:
            return n
    return None


def is_mdl_mdh(tok):
    if tok == 'MDL':
        return MDL_ADDR
    if tok == 'MDH':
        return MDH_ADDR
    return None


class Var:
    __slots__ = ('name', 'size', 'addr')

    def __init__(self, name):
        self.name = name
        self.size = 2
        self.addr = None


class Asm:
    def __init__(self):
        self.lines = []          # (lineno, mnemonic, [operands]) após macro-expansão
        self.labels = {}         # nome -> índice em self.lines (endereço resolvido no passe 2)
        self.vars = {}           # nome -> Var
        self.equs = {}           # nome -> endereço fixo (diretiva EQU, ver load_file)
        self.src_lineno = []     # para mensagens de erro

    @staticmethod
    def _parse_equ_value(tok):
        """Valor do lado direito de um EQU. c167cc emite endereço @ram()
        no formato Intel `0E2FAH` (dígito inicial 0 pra não colidir com
        identificador, sufixo H) - ver docs/memory-model.md do compiler/.
        Aceita também `0x..`/decimal solto, pra EQU escrito à mão."""
        tok = tok.strip()
        if tok[-1:] in ('H', 'h') and re.match(r'^0[0-9A-Fa-f]*[Hh]$', tok):
            return int(tok[:-1], 16)
        if tok.lower().startswith('0x'):
            return int(tok, 16)
        return int(tok, 10)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    def parse_operand_symbol(self, tok):
        """Retorna ('reg', n) | ('mdl',) | ('mdh',) | ('mem', nome, offset) | ('imm', valor)
        | ('imm_addr', nome) | ('ind', n, modo)."""
        tok = tok.strip()
        if tok.startswith('#'):
            val = tok[1:]
            if val.lower().startswith('0x'):
                return ('imm', int(val, 16))
            try:
                return ('imm', int(val, 10))
            except ValueError:
                # `#NOME` de símbolo (não numérico) - endereço da variável,
                # não seu conteúdo (ex.: carregar Rw com o endereço-base de
                # um array pra endereçamento indireto `[Rw]` - ver `ind`
                # abaixo). Resolvido só na passagem 2, quando os endereços
                # de variável já estão alocados (ver `resolve_mem_addr`).
                return ('imm_addr', val)
        m = re.match(r'^\[\s*([Rr]\d{1,2})\s*\+\s*#\s*(-?0[Xx][0-9A-Fa-f]+|-?\d+)\s*\]$', tok)
        if m:
            # `[Rm+#N]` - indireto com deslocamento de 16 bits (opcodes
            # 0xD4/0xC4, já implementados em c166sim.py) - mesma sintaxe
            # exata que o c167cc emite pra acesso a parâmetro/local de pilha
            # (`[R15+#0]` etc, ver compiler/docs/abi.md) - patch desta sessão
            # pra permitir montar a ABI real do compilador, não só código
            # escrito à mão com endereço fixo.
            off_tok = m.group(2)
            base = 16 if off_tok.lower().lstrip('-').startswith('0x') else 10
            return ('ind_off', is_reg(m.group(1)), int(off_tok, base))
        m = re.match(r'^\[\s*([Rr]\d{1,2})\s*\+\s*\]$', tok)
        if m:
            return ('ind', is_reg(m.group(1)), 'postinc')
        m = re.match(r'^\[\s*-\s*([Rr]\d{1,2})\s*\]$', tok)
        if m:
            return ('ind', is_reg(m.group(1)), 'predec')
        m = re.match(r'^\[\s*([Rr]\d{1,2})\s*\]$', tok)
        if m:
            return ('ind', is_reg(m.group(1)), 'plain')
        nib = is_breg(tok)
        if nib is not None:
            return ('breg', nib)
        n = is_reg(tok)
        if n is not None:
            return ('reg', n)
        if tok == 'MDL':
            return ('mem_named', 'MDL', 0)
        if tok == 'MDH':
            return ('mem_named', 'MDH', 0)
        if tok == 'SP':
            return ('mem_named', 'SP', 0)
        if '+' in tok:
            name, off = tok.split('+', 1)
            return ('mem_named', name.strip(), int(off.strip()))
        return ('mem_named', tok, 0)

    @staticmethod
    def _strip_cc(tok):
        cc_name = tok.strip()
        if cc_name.lower().startswith('cc_'):
            cc_name = cc_name[3:]
        return cc_name.upper()

    @staticmethod
    def _abs_addr_literal(name):
        """Se `name` for um literal numérico (0x.... ou decimal), trata como
        endereço absoluto fixo (ex.: SFR de UART) em vez de nome de variável
        alocada automaticamente após o código - ver resolve_mem_addr. Só
        símbolos puramente numéricos entram aqui; nomes de variável comuns
        (ex. `NUMERO`) continuam sendo alocados como antes."""
        try:
            base = 16 if name.lower().startswith('0x') else 10
            return int(name, base)
        except ValueError:
            return None

    def note_var(self, name, offset):
        if name in ('MDL', 'MDH', 'SP'):
            return
        if self._abs_addr_literal(name) is not None:
            return
        if name in self.equs:
            return
        if name in self.labels:
            # já é um label real (código OU dado inicializado via `DW`,
            # ver expand_line) - não aloca uma variável de 2 bytes
            # fantasma por cima; o endereço de verdade já vem de
            # `label_addr` em resolve_mem_addr. Achado 21/08/2026: sem essa
            # guarda, `MOV Rn,#NOME`/`MOV Rn,NOME` sobre um array `DW`
            # (emitido pelo `c167cc` pra inicializador agregado) ficava
            # listado 2x - uma vez como label (endereço certo) e outra como
            # var fantasma (2 bytes, nunca usada de fato, só poluía o
            # relatório do montador).
            return
        v = self.vars.setdefault(name, Var(name))
        v.size = max(v.size, offset + 2)

    def load_file(self, path):
        with open(path) as f:
            raw = f.readlines()
        pending_label = None
        for i, raw_line in enumerate(raw, 1):
            line = raw_line.split(';', 1)[0].strip()
            if not line:
                continue
            # diretiva EQU ("NOME EQU 0E2FAH") - c167cc emite uma dessas por
            # global @ram()/@rom() (ver compiler/docs/memory-model.md); tem
            # que ser checada ANTES do parsing genérico de mnemônico, senão
            # "NOME" vira mnemônico desconhecido (maiúsculado) e "EQU val"
            # vira um operando só sem vírgula. Não aloca espaço nem emite
            # bytes - só registra um alias de endereço fixo, igual
            # _abs_addr_literal mas por NOME em vez de literal numérico cru.
            m_equ = re.match(r'^(\w+)\s+EQU\s+(\S+)\s*$', line, re.IGNORECASE)
            if m_equ:
                self.equs[m_equ.group(1)] = self._parse_equ_value(m_equ.group(2))
                continue
            # rótulo sozinho na linha ("LOOP:")
            if line.endswith(':') and ' ' not in line and '\t' not in line:
                self.labels[line[:-1]] = len(self.lines)
                continue
            # rótulo seguido de instrução na mesma linha
            m = re.match(r'^(\w+):\s*(.*)$', line)
            if m and m.group(2):
                self.labels[m.group(1)] = len(self.lines)
                line = m.group(2)
            elif m:
                self.labels[m.group(1)] = len(self.lines)
                continue

            parts = line.split(None, 1)
            mnemonic = parts[0].upper()
            operands = []
            if len(parts) > 1:
                operands = [o.strip() for o in parts[1].split(',')]

            self.expand_line(mnemonic, operands, i)

    # ------------------------------------------------------------------
    # Expansão de pseudo-instruções em instruções reais da C166
    # ------------------------------------------------------------------
    def emit(self, mnemonic, operands, src_lineno):
        self.lines.append((mnemonic, operands))
        self.src_lineno.append(src_lineno)

    def expand_line(self, mnemonic, operands, src_lineno):
        if mnemonic == 'RESERVE':
            # "RESERVE NOME, #N" - diretiva de montagem pura (não emite
            # código): reserva N bytes contíguos pra NOME, pra arrays
            # endereçados via [Rw+] (patch desta sessão) em vez de uma
            # variável escalar comum de 2 bytes. Não existe na C166 real -
            # é só uma forma de dizer ao montador "aloque este tanto".
            name_tok, size_tok = operands
            assert size_tok.strip().startswith('#'), "RESERVE espera #N de bytes"
            size = int(size_tok.strip()[1:], 0)
            v = self.vars.setdefault(name_tok.strip(), Var(name_tok.strip()))
            v.size = max(v.size, size)
            return

        if mnemonic == 'DW':
            # "NAME: DW v1,v2,v3" - dado inicializado (word por valor),
            # emitido pelo `c167cc` pra globais com inicializador agregado
            # constante (`= {1,2,{3,4}}`, ver flatten_init_list no
            # compilador) - diferente de RESERVE (que só reserva espaço
            # zerado), isso é uma linha "de código" de verdade (ocupa
            # endereço real na sequência de instruções, resolvido como
            # label - ver resolve_mem_addr) com bytes literais no .bin.
            values = []
            for tok in operands:
                tok = tok.strip()
                values.append(int(tok, 0))
            self.emit('DW', [values], src_lineno)
            return

        ops = [self.parse_operand_symbol(o) for o in operands]

        if mnemonic == 'MOV':
            dst, src = ops
            self._expand_mov(dst, src, src_lineno)
            return

        if mnemonic in ('ADD', 'SUB') and len(ops) == 2 and ops[0] == ('mem_named', 'SP', 0) and ops[1][0] == 'imm':
            # "ADD SP,#N" / "SUB SP,#N" - forma usada pelo prólogo/epílogo
            # de pilha do c167cc (docs/abi.md: "ADD SP,#6" liberando
            # locais+spills) - não existe ALU direto sobre SFR-como-memória
            # com imediato neste montador, expande via scratch.
            #
            # NÃO usar R6/R7 (SCRATCH_MEM/SCRATCH_MUL) aqui - achado
            # 21/08/2026 depurando o 1º teste real de retorno de struct por
            # valor (sret): o PRÓLOGO de uma função (`PUSH R15; SUB SP,#N;
            # MOV R15,SP; MOV [R15+#0],R4; ...`) executa o ajuste de SP
            # ANTES de salvar os parâmetros recebidos em R4-R7 (convenção
            # do c167cc, docs/abi.md) pra dentro da pilha - se a expansão
            # de SUB SP usar R6/R7 como rascunho, ela DESTRÓI o 3º/4º
            # parâmetro recebido antes dele ser salvo (bug real, causava
            # `MOV R0,[R15+#4]` devolver lixo em vez do parâmetro certo).
            # R13/R14 são "reservados pra uso futuro" pelo próprio
            # compilador (docs/assembly-syntax.md-adjacent comment em
            # isa.c) - ninguém mais usa, seguro como rascunho aqui.
            SP_SCRATCH_A, SP_SCRATCH_B = 13, 14
            self.emit('MOV', [('reg', SP_SCRATCH_A), ('mem_named', 'SP', 0)], src_lineno)
            self.emit('MOV', [('reg', SP_SCRATCH_B), ops[1]], src_lineno)
            self.emit(mnemonic, [('reg', SP_SCRATCH_A), ('reg', SP_SCRATCH_B)], src_lineno)
            self.emit('MOV', [('mem_named', 'SP', 0), ('reg', SP_SCRATCH_A)], src_lineno)
            return

        if mnemonic in ('MUL', 'MULU'):
            # Forma real: "MUL/MULU op1, op2", dois GPRs explícitos (MDH:MDL =
            # op1*op2, sempre sobrescrito - MDL/MDH prévios não importam).
            # Carrega em R6/R7 quando op1/op2 forem endereço de memória.
            # MULU preserva o mnemônico (achado 02/09/2026: renomear pra MUL
            # incondicionalmente, como o port_real_abi.py do projeto irmão
            # fazia, muda o resultado de verdade sempre que um operando tem
            # o bit 15 setado - MUL trata como negativo, MULU não. MDL (só
            # a metade baixa) coincide nos dois casos, então esse bug ficava
            # invisível até algo ler MDH também).
            if len(ops) == 2:
                op1 = self._reg_of(ops[0], SCRATCH_MUL, src_lineno)
                op2 = self._reg_of(ops[1], SCRATCH_MEM, src_lineno)
                self.emit(mnemonic, [op1, op2], src_lineno)
                return
            # Forma pseudo legada de 1 operando: "MUL X" com MDL
            # pré-carregado pela linha anterior "MOV MDL, Y" - expande pra
            # MUL <reg-de-Y>, <reg-de-X>.
            (src,) = ops
            op1 = self._last_mdl_src if self._last_mdl_src is not None else ('reg', SCRATCH_MUL)
            op2 = self._reg_of(src, SCRATCH_MEM, src_lineno)
            self.emit(mnemonic, [('reg', op1[1]), ('reg', op2[1])], src_lineno)
            return

        if mnemonic in ('DIV', 'DIVU', 'DIVL', 'DIVLU'):
            (src,) = ops
            op = self._reg_of(src, SCRATCH_MUL, src_lineno)
            self.emit(mnemonic, [('reg', op[1])], src_lineno)
            return

        if mnemonic == 'DEC':
            # DEC não existe na C166 real -> SUB Rw, #1 (mesmo efeito, flags
            # padrão de subtração ao invés de flags específicas de DEC).
            (dst,) = ops
            assert dst[0] == 'reg', f"linha {src_lineno}: DEC só suporta registrador"
            self.emit('SUB', [dst, ('imm', 1)], src_lineno)
            return

        if mnemonic == 'JMPR':
            # forma real nativa: "JMPR cc_XX, target" (mesmo formato usado
            # pelo desmontador c166dis.py)
            cc_tok, target = operands
            self.emit('JMPR', [self._strip_cc(cc_tok), target], src_lineno)
            return
        if mnemonic == 'JMP':
            (target,) = operands
            self.emit('JMPR', ['UC', target], src_lineno)
            return
        if mnemonic == 'JLE':
            (target,) = operands
            self.emit('JMPR', ['SLE', target], src_lineno)
            return
        if mnemonic == 'JGE':
            (target,) = operands
            self.emit('JMPR', ['SGE', target], src_lineno)
            return

        if mnemonic == 'NOP':
            self.emit('NOP', [], src_lineno)
            return

        if mnemonic in ('SRVWDT', 'EINIT', 'SRST'):
            self.emit(mnemonic, [], src_lineno)
            return

        if mnemonic in ('RET', 'RETS'):
            self.emit(mnemonic, [], src_lineno)
            return

        if mnemonic in ('PUSH', 'POP', 'CALLR'):
            if mnemonic == 'CALLR':
                (target,) = operands
                self.emit('CALLR', [target], src_lineno)
                return
            (reg,) = ops
            assert reg[0] == 'reg', f"linha {src_lineno}: {mnemonic} só suporta registrador"
            self.emit(mnemonic, [reg], src_lineno)
            return

        if mnemonic in ('JMPA', 'CALLA'):
            # forma real "JMPA cc_XX, target" / "CALLA cc_XX, target" - alvo
            # absoluto de 16 bits, mesmo segmento (CSP intocado)
            cc_tok, target = operands
            self.emit(mnemonic, [self._strip_cc(cc_tok), target], src_lineno)
            return

        if mnemonic in ('JMPI', 'CALLI'):
            # "JMPI cc_XX, [Rw]" / "CALLI cc_XX, [Rw]" - indireto, mesmo segmento.
            # Também aceita "Rw" sem colchetes (achado 21/08/2026: o `c167cc`
            # emite chamada indireta através de ponteiro de função assim,
            # sem colchete - mesmo significado, sintaxe mais solta).
            cc_tok, rw_tok = operands
            rw_tok = rw_tok.strip()
            m = re.match(r'^\[\s*([Rr]\d{1,2})\s*\]$', rw_tok) or re.match(r'^([Rr]\d{1,2})$', rw_tok)
            assert m, f"linha {src_lineno}: {mnemonic} precisa de [Rw] ou Rw como 2º operando"
            n = is_reg(m.group(1))
            assert n is not None
            self.emit(mnemonic, [self._strip_cc(cc_tok), ('reg', n)], src_lineno)
            return

        if mnemonic in ('JMPS', 'CALLS'):
            # "JMPS seg=0xNN, target" / "CALLS seg=0xNN, target" - troca de
            # segmento de verdade. target pode ser rótulo (resolvido no
            # segmento montado, sempre 0 pros nossos programas) ou um
            # endereço numérico cru (0x.... ou decimal), pra apontar pra
            # bytes fora do que este assembler gerou (ex.: simular firmware
            # real carregado em outro segmento da mesma imagem).
            seg_tok, target = operands
            m = re.match(r'^seg\s*=\s*(.+)$', seg_tok.strip(), re.IGNORECASE)
            assert m, f"linha {src_lineno}: {mnemonic} precisa de 'seg=...' como 1º operando"
            seg_val = m.group(1).strip()
            seg = int(seg_val, 16 if seg_val.lower().startswith('0x') else 10)
            self.emit(mnemonic, [seg, target], src_lineno)
            return

        if mnemonic == 'MOVB':
            dst, src = ops
            # c167cc emite operando de registrador de byte como "Rn" puro
            # (registrador de PALAVRA inteiro, não "RLn/RHn") mesmo em
            # contexto MOVB - achado 21/08/2026 compilando structs/arrays de
            # uint8_t pela 1ª vez: o alocador de registrador do compilador
            # só conhece slots de palavra inteira, então usa a mesma
            # convenção de nome pra qualquer largura, e deixa o MOVB (que
            # só toca no byte baixo) fazer a diferença. Normaliza "Rn" ->
            # RLn (breg, sel=0=baixo) só nesse contexto, pra bater com o
            # que a C166 real espera - forma indireta ([Rw]/[Rw+#N]) some
            # aqui do mesmo jeito que MOV normal (ver 'ind'/'ind_off').
            if dst[0] == 'reg':
                dst = ('breg', dst[1] << 1)
            if src[0] == 'reg':
                src = ('breg', src[1] << 1)
            if dst[0] == 'mem_named':
                self.note_var(dst[1], dst[2])
            if src[0] == 'mem_named':
                self.note_var(src[1], src[2])
            self.emit('MOVB', [dst, src], src_lineno)
            return

        # instruções já compatíveis 1:1 com a real (ADD/SUB/CMP/SHR/NEG/MOV
        # já tratado acima). Registra qualquer operando de memória aqui -
        # antes só MOV/MUL/DIV chamavam note_var, então um símbolo citado só
        # numa CMP/ADDB/etc (nunca num MOV) ficaria de fora de self.vars e
        # quebraria resolve_mem_addr no passe 2.
        for o in ops:
            if o[0] == 'mem_named':
                self.note_var(o[1], o[2])
        self.emit(mnemonic, ops, src_lineno)

    _last_mdl_src = None

    def _reg_of(self, operand, scratch_n, src_lineno):
        """Garante que `operand` esteja num registrador real, emitindo um MOV
        scratch<-mem antes se necessário. Retorna ('reg', n)."""
        if operand[0] == 'reg':
            return operand
        # mem_named
        _, name, off = operand
        self.note_var(name, off)
        self.emit('MOV', [('reg', scratch_n), operand], src_lineno)
        return ('reg', scratch_n)

    def _expand_mov(self, dst, src, src_lineno):
        if dst[0] == 'mem_named':
            self.note_var(dst[1], dst[2])
        if src[0] == 'mem_named':
            self.note_var(src[1], src[2])
        if src[0] == 'imm_addr':
            self.note_var(src[1], 0)

        if dst[0] == 'reg' and src[0] == 'ind':
            # MOV Rn,[Rm] / MOV Rn,[Rm+] - endereçamento indireto por
            # registrador (opcodes 0xA8/0x98 já implementados em
            # c166sim.py; sem offset - ver comentário no patch desta sessão)
            self.emit('MOV', [dst, src], src_lineno)
            return
        if dst[0] == 'ind' and src[0] == 'reg':
            # MOV [Rm],Rn / MOV [-Rm],Rn (opcodes 0xB8/0x88)
            self.emit('MOV', [dst, src], src_lineno)
            return
        if dst[0] == 'reg' and src[0] == 'ind_off':
            # MOV Rn,[Rm+#N] - indireto com deslocamento (opcode 0xD4) -
            # forma real da ABI de pilha do c167cc (ver docs/abi.md)
            self.emit('MOV', [dst, src], src_lineno)
            return
        if dst[0] == 'ind_off' and src[0] == 'reg':
            # MOV [Rm+#N],Rn (opcode 0xC4)
            self.emit('MOV', [dst, src], src_lineno)
            return

        if dst[0] == 'reg' and src[0] in ('reg', 'imm', 'imm_addr', 'mem_named'):
            self.emit('MOV', [dst, src], src_lineno)
            if src[0] == 'mem_named' and src[1] == 'MDL':
                # rastreia a origem que foi carregada em MDL (não se aplica
                # aqui pois dst é reg, não MDL - mantido por simetria)
                pass
            return

        if dst[0] == 'mem_named' and dst[1] == 'MDL' and src[0] == 'reg':
            # MOV MDL, Rn real e direto; guarda Rn como op1 implícito p/ MUL
            self.emit('MOV', [dst, src], src_lineno)
            self._last_mdl_src = src
            return

        if dst[0] == 'mem_named' and src[0] == 'reg':
            # mem <- reg : forma real direta (memreg)
            self.emit('MOV', [dst, src], src_lineno)
            return

        if dst[0] == 'mem_named' and dst[1] == 'MDL' and src[0] in ('imm', 'mem_named'):
            # MOV MDL, <imm ou mem> -> carrega scratch e usa como op1 do MUL
            self.emit('MOV', [('reg', SCRATCH_MUL), src], src_lineno)
            self.emit('MOV', [dst, ('reg', SCRATCH_MUL)], src_lineno)
            self._last_mdl_src = ('reg', SCRATCH_MUL)
            return

        if dst[0] == 'mem_named' and src[0] in ('imm', 'mem_named'):
            # MOV mem,#imm ou MOV mem,mem -> não existem na C166 real,
            # passa por R6 (scratch genérico)
            self.emit('MOV', [('reg', SCRATCH_MEM), src], src_lineno)
            self.emit('MOV', [dst, ('reg', SCRATCH_MEM)], src_lineno)
            return

        raise AssertionError(f"linha {src_lineno}: combinação MOV não tratada: {dst} <- {src}")

    # ------------------------------------------------------------------
    # Passe 1: tamanho de cada instrução (todas fixas em 2 ou 4 bytes)
    # ------------------------------------------------------------------
    INSN_LEN = {
        ('MOV', 'RR'): 2, ('MOV', 'Ri'): 2, ('MOV', 'RI'): 4,
        ('MOV', 'Rm'): 4, ('MOV', 'mR'): 4,
        ('MOV', 'RP'): 2, ('MOV', 'PR'): 2,  # indireto por registrador [Rw] (patch desta sessão)
        ('MOV', 'RA'): 4,  # reg <- #NOME (endereço de variável, não seu conteúdo)
        ('MOV', 'RO'): 4, ('MOV', 'OR'): 4,  # [Rw+#offset] (ABI de pilha do c167cc)
        ('ADD', 'RR'): 2, ('ADD', 'RI'): 4, ('SUB', 'RR'): 2, ('SUB', 'RI'): 4,
        ('AND', 'RI'): 4, ('OR', 'RI'): 4, ('XOR', 'RI'): 4,
        ('ADDC', 'RR'): 2, ('SUBC', 'RR'): 2,
        ('CMP', 'RR'): 2, ('CMP', 'RI'): 4, ('CMP', 'Rm'): 4,
        ('MUL', 'RR'): 2, ('MULU', 'RR'): 2, ('DIV', 'R'): 2,
        ('SHR', 'RR'): 2, ('SHR', 'Ri'): 2,
        ('ASHR', 'RR'): 2, ('ASHR', 'Ri'): 2,
        ('SHL', 'RR'): 2, ('SHL', 'Ri'): 2,
        ('OR', 'RR'): 2, ('AND', 'RR'): 2, ('XOR', 'RR'): 2,
        ('NEG', 'R'): 2, ('CPL', 'R'): 2,
        ('MOVB', 'BB'): 2, ('MOVB', 'Bi'): 2, ('MOVB', 'Bm'): 4, ('MOVB', 'mB'): 4,
        ('MOVB', 'BP'): 2, ('MOVB', 'PB'): 2,  # indireto por registrador [Rw] sem offset
        ('MOVB', 'BO'): 4, ('MOVB', 'OB'): 4,  # [Rw+#offset]
        ('ADDB', 'BB'): 2, ('ADDB', 'Bm'): 4, ('ADDB', 'mB'): 4,
        ('SUBB', 'BB'): 2, ('SUBB', 'Bm'): 4, ('SUBB', 'mB'): 4,
        ('ANDB', 'BB'): 2, ('ANDB', 'Bm'): 4, ('ANDB', 'mB'): 4,
        ('ORB', 'BB'): 2, ('ORB', 'Bm'): 4, ('ORB', 'mB'): 4,
        ('CMPB', 'BB'): 2, ('CMPB', 'Bm'): 4,
        ('MOVBZ', 'RB'): 2, ('MOVBZ', 'Rm'): 4,
        ('NOP', ''): 2,
        ('JMPR', 'cc'): 2,
        ('SRVWDT', ''): 4, ('EINIT', ''): 4, ('SRST', ''): 4,
        ('RET', ''): 2,
        ('PUSH', 'R'): 2, ('POP', 'R'): 2,
        ('CALLR', 'cc'): 2,
    }

    def operand_shape(self, mnemonic, operands):
        # CMP só tem forma real de imediato de 16 bits (CMP Rw,#data16,
        # opcode 0x46) - não existe CMP Rw,#data4 - então força sempre 'I'.
        # ADD Rw,#data4 curto não existe na C166 real (só reg,reg e
        # reg,#data16, opcode 0x06) - diferente de MOV, que tem a forma
        # curta E0 - por isso ADD imediato sempre usa a forma larga aqui
        # (achado 21/08/2026 compilando código real do c167cc pela 1ª vez,
        # que gera `ADD Rw,#N` livremente pra qualquer N pequeno).
        force_wide_imm = mnemonic in ('CMP', 'SUB', 'ADD', 'AND', 'OR', 'XOR')
        shape = ''
        for o in operands:
            if o[0] == 'reg':
                shape += 'R'
            elif o[0] == 'imm':
                shape += 'I' if force_wide_imm or o[1] > 15 or o[1] < 0 else 'i'
            elif o[0] == 'mem_named':
                shape += 'm'
            elif o[0] == 'breg':
                shape += 'B'
            elif o[0] == 'ind':
                shape += 'P'
            elif o[0] == 'imm_addr':
                shape += 'A'
            elif o[0] == 'ind_off':
                shape += 'O'
        return shape

    def sizeof(self, mnemonic, operands):
        if mnemonic in ('JMPR', 'CALLR'):
            return 2
        if mnemonic == 'NOP':
            return 2
        if mnemonic in ('SRVWDT', 'EINIT', 'SRST'):
            return 4
        if mnemonic in ('JMPA', 'CALLA', 'JMPS', 'CALLS'):
            return 4
        if mnemonic in ('JMPI', 'CALLI', 'RETS'):
            return 2
        if mnemonic == 'DW':
            (values,) = operands
            return 2 * len(values)
        shape = self.operand_shape(mnemonic, operands)
        # normaliza: qualquer imediato de MOV vira 'I' (regd16, 4 bytes) pra
        # simplificar - exceto Rwd4 (0-15) explicitamente reconhecido como 'i'
        key = (mnemonic, shape)
        if key not in self.INSN_LEN:
            raise AssertionError(f"tamanho desconhecido p/ {mnemonic} {operands} (shape={shape})")
        return self.INSN_LEN[key]

    # ------------------------------------------------------------------
    # Resolve endereços (labels = índice de instrução -> endereço de byte;
    # variáveis alocadas após o código)
    # ------------------------------------------------------------------
    def assemble(self, org=0):
        # 1) endereço de cada instrução
        addrs = []
        pc = org
        for mnemonic, operands in self.lines:
            addrs.append(pc)
            pc += self.sizeof(mnemonic, operands)
        code_end = pc

        label_addr = {name: addrs[idx] if idx < len(addrs) else pc
                      for name, idx in self.labels.items()}

        # 2) aloca variáveis logo após o código, alinhadas em 2
        data_pc = (code_end + 1) & ~1
        for name in sorted(self.vars):
            v = self.vars[name]
            v.addr = data_pc
            data_pc += (v.size + 1) & ~1

        out = bytearray()
        for idx, (mnemonic, operands) in enumerate(self.lines):
            out += self.encode(mnemonic, operands, addrs[idx], label_addr)
        assert len(out) == code_end - org
        # preenche até o começo da área de dados (não é usado como código,
        # só mantém o arquivo como uma imagem de memória plana e contígua)
        out += bytes(data_pc - code_end)
        return bytes(out), label_addr, {n: v.addr for n, v in self.vars.items()}, code_end, data_pc

    def resolve_mem_addr(self, name, off, label_addr):
        if name == 'MDL':
            return MDL_ADDR + off
        if name == 'MDH':
            return MDH_ADDR + off
        if name == 'SP':
            return SP_ADDR + off
        abs_addr = self._abs_addr_literal(name)
        if abs_addr is not None:
            return abs_addr + off
        if name in self.equs:
            return self.equs[name] + off
        if name in self.vars:
            return self.vars[name].addr + off
        if name in label_addr:
            # dado inicializado (diretiva `DW`, ver expand_line) vive no
            # mesmo espaço de endereço que código/labels, não em self.vars -
            # patch desta sessão pra suportar o `c167cc` emitir globais com
            # inicializador constante em vez de só `DS` zerado.
            return label_addr[name] + off
        raise AssertionError(f"símbolo de memória desconhecido: {name}")

    # ------------------------------------------------------------------
    # Passe 2: codifica opcode real (mesma tabela do c166dis.py)
    # ------------------------------------------------------------------
    def encode(self, mnemonic, operands, pc, label_addr):
        if mnemonic == 'JMPR':
            cc_name, target = operands
            cc = CC_MAP[cc_name]
            opcode = (cc << 4) | 0xD
            tgt = label_addr[target]
            rel = (tgt - (pc + 2)) // 2
            assert -128 <= rel <= 127, f"salto para {target} fora do alcance de JMPR (rel={rel})"
            return bytes([opcode, rel & 0xFF])

        if mnemonic in ('JMPA', 'CALLA'):
            cc_name, target = operands
            cc = CC_MAP[cc_name]
            caddr = self._resolve_target(target, label_addr)
            return bytes([0xEA if mnemonic == 'JMPA' else 0xCA, cc << 4, caddr & 0xFF, (caddr >> 8) & 0xFF])

        if mnemonic in ('JMPI', 'CALLI'):
            cc_name, rw = operands
            cc = CC_MAP[cc_name]
            return bytes([0x9C if mnemonic == 'JMPI' else 0xAB, (cc << 4) | rw[1]])

        if mnemonic in ('JMPS', 'CALLS'):
            seg, target = operands
            caddr = self._resolve_target(target, label_addr)
            return bytes([0xFA if mnemonic == 'JMPS' else 0xDA, seg & 0xFF, caddr & 0xFF, (caddr >> 8) & 0xFF])

        if mnemonic == 'RETS':
            return bytes([0xDB, 0x00])  # "RETS  DB 00" (manual Infineon)

        if mnemonic == 'NOP':
            return bytes([0xCC, 0x00])

        if mnemonic == 'DW':
            (values,) = operands
            out = bytearray()
            for v in values:
                out += bytes([v & 0xFF, (v >> 8) & 0xFF])
            return bytes(out)

        if mnemonic == 'NEG':
            (r,) = operands
            return bytes([0x81, 0xF0 | r[1]])

        if mnemonic == 'CPL':
            (r,) = operands
            return bytes([0x91, 0xF0 | r[1]])

        if mnemonic == 'DIV':
            (r,) = operands
            return bytes([0x4B, 0xF0 | r[1]])

        if mnemonic == 'RET':
            return bytes([0xCB, 0x00])  # "RET  CB 00" (manual Infineon)

        if mnemonic == 'PUSH':
            (r,) = operands
            return bytes([0xEC, 0xF0 | r[1]])

        if mnemonic == 'POP':
            (r,) = operands
            return bytes([0xFC, 0xF0 | r[1]])

        if mnemonic == 'CALLR':
            (target,) = operands
            tgt = label_addr[target]
            rel = (tgt - (pc + 2)) // 2
            assert -128 <= rel <= 127, f"CALLR para {target} fora do alcance (rel={rel})"
            return bytes([0xBB, rel & 0xFF])

        if mnemonic in ('SRVWDT', 'EINIT', 'SRST'):
            # instruções protegidas: o manual Infineon exige esse padrão
            # exato de 4 bytes (opcode + byte de proteção + opcode repetido
            # 2x), não zero-padding - "SRVWDT A7 58 A7 A7", "EINIT B5 4A B5
            # B5", "SRST B7 48 B7 B7"
            return {
                'SRVWDT': bytes([0xA7, 0x58, 0xA7, 0xA7]),
                'EINIT': bytes([0xB5, 0x4A, 0xB5, 0xB5]),
                'SRST': bytes([0xB7, 0x48, 0xB7, 0xB7]),
            }[mnemonic]

        if mnemonic in ('MOV', 'ADD', 'SUB', 'CMP', 'MUL', 'MULU', 'SHR', 'ASHR', 'SHL', 'OR', 'AND', 'XOR', 'ADDC', 'SUBC'):
            return self._encode_alu(mnemonic, operands, label_addr)

        if mnemonic in ('MOVB', 'ADDB', 'SUBB', 'ANDB', 'ORB', 'CMPB'):
            return self._encode_byteop(mnemonic, operands, label_addr)

        if mnemonic == 'MOVBZ':
            dst, src = operands
            assert dst[0] == 'reg'
            if src[0] == 'breg':
                # "C0 mn": nibble ALTO = m (fonte Rb), BAIXO = n (destino Rw) -
                # ordem invertida em relação a toda outra forma "XX nm" da
                # tabela, confirmada contra a imagem real (ver c166dis.py)
                return bytes([0xC0, (src[1] << 4) | dst[1]])
            lo, hi = self._mem_bytes(src, label_addr)
            return bytes([0xC2, 0xF0 | dst[1], lo, hi])

        raise AssertionError(f"mnemônico não suportado no encoder: {mnemonic}")

    def _resolve_target(self, target, label_addr):
        """Alvo de JMPA/CALLA/JMPS/CALLS: rótulo conhecido, ou endereço numérico
        cru (0x.../decimal) pra apontar fora do que este assembler montou -
        ex.: simular um trecho de firmware real carregado noutro segmento."""
        if target in label_addr:
            return label_addr[target]
        base = 16 if target.lower().startswith('0x') else 10
        return int(target, base) & 0xFFFF

    def _mem_bytes(self, operand, label_addr):
        _, name, off = operand
        addr = self.resolve_mem_addr(name, off, label_addr)
        return addr & 0xFF, (addr >> 8) & 0xFF

    def _encode_byteop(self, mnemonic, operands, label_addr):
        # RbRb ("XX nm": destino=nibble alto, fonte=nibble baixo - mesma ordem
        # de todo resto da tabela, só o MOVBZ acima é invertido) e as formas
        # reg<->mem (regfield byte sempre 0xF0|nibble já que nosso assembler só
        # emite registrador de byte de GPR nesse campo, nunca SFR-como-byte).
        opcodes = {
            'MOVB': {'BB': 0xF1, 'Bm': 0xF3, 'mB': 0xF7},
            'ADDB': {'BB': 0x01, 'Bm': 0x03, 'mB': 0x05},
            'SUBB': {'BB': 0x21, 'Bm': 0x23, 'mB': 0x25},
            'ANDB': {'BB': 0x61, 'Bm': 0x63, 'mB': 0x65},
            'ORB': {'BB': 0x71, 'Bm': 0x73, 'mB': 0x75},
            'CMPB': {'BB': 0x41, 'Bm': 0x43},
        }[mnemonic]
        dst, src = operands
        if mnemonic == 'MOVB' and dst[0] == 'breg' and src[0] == 'imm':
            # "E1 #n": nibble alto = imediato de 4 bits, baixo = registrador de
            # byte destino - mesma correção já aplicada em MOV Rw,#data4
            assert 0 <= src[1] <= 15, "MOVB Rb,#imm só suporta 0-15 (forma E1 #n de 4 bits)"
            return bytes([0xE1, (src[1] << 4) | dst[1]])
        if dst[0] == 'breg' and src[0] == 'breg':
            return bytes([opcodes['BB'], (dst[1] << 4) | src[1]])
        if dst[0] == 'breg' and src[0] == 'mem_named':
            lo, hi = self._mem_bytes(src, label_addr)
            return bytes([opcodes['Bm'], 0xF0 | dst[1], lo, hi])
        if dst[0] == 'mem_named' and src[0] == 'breg':
            lo, hi = self._mem_bytes(dst, label_addr)
            return bytes([opcodes['mB'], 0xF0 | src[1], lo, hi])
        if mnemonic == 'MOVB' and dst[0] == 'breg' and src[0] == 'ind':
            # "A9 nm" (plain [Rm]) / "99 nm" (pós-incrementa Rm em 1 byte)
            m, mode = src[1], src[2]
            op = 0x99 if mode == 'postinc' else 0xA9
            assert mode in ('plain', 'postinc'), "MOVB Rb,[Rm] não suporta pré-decremento como origem"
            return bytes([op, (dst[1] << 4) | m])
        if mnemonic == 'MOVB' and dst[0] == 'ind' and src[0] == 'breg':
            # "B9 nm" (plain) / "89 nm" (pré-decrementa Rm em 1 byte)
            m, mode = dst[1], dst[2]
            op = 0x89 if mode == 'predec' else 0xB9
            assert mode in ('plain', 'predec'), "MOVB [Rm],Rb não suporta pós-incremento como destino"
            return bytes([op, (src[1] << 4) | m])
        if mnemonic == 'MOVB' and dst[0] == 'breg' and src[0] == 'ind_off':
            # "F4 nm ## ##" - MOVB Rbn,[Rwm+#data16]
            m, disp = src[1], src[2] & 0xFFFF
            return bytes([0xF4, (dst[1] << 4) | m, disp & 0xFF, (disp >> 8) & 0xFF])
        if mnemonic == 'MOVB' and dst[0] == 'ind_off' and src[0] == 'breg':
            # "E4 nm ## ##" - MOVB [Rwm+#data16],Rbn
            m, disp = dst[1], dst[2] & 0xFFFF
            return bytes([0xE4, (src[1] << 4) | m, disp & 0xFF, (disp >> 8) & 0xFF])
        raise AssertionError(f"forma de {mnemonic} não codificável: {dst} <- {src}")

    def _encode_alu(self, mnemonic, operands, label_addr):
        if mnemonic == 'MOV':
            dst, src = operands
            if dst[0] == 'reg' and src[0] == 'reg':
                return bytes([0xF0, (dst[1] << 4) | src[1]])
            if dst[0] == 'reg' and src[0] == 'imm':
                if 0 <= src[1] <= 15:
                    # formato real "E0 #n": nibble alto = imediato, baixo =
                    # registrador (achado batendo contra o módulo C166 do
                    # Ghidra e o manual Infineon - ver mesmo bug corrigido
                    # em c166dis.py/is_byte_mn... er, em decode_one 'Rwd4')
                    return bytes([0xE0, (src[1] << 4) | dst[1]])
                lo, hi = src[1] & 0xFF, (src[1] >> 8) & 0xFF
                return bytes([0xE6, 0xF0 | dst[1], lo, hi])
            if dst[0] == 'reg' and src[0] == 'mem_named':
                lo, hi = self._mem_bytes(src, label_addr)
                return bytes([0xF2, 0xF0 | dst[1], lo, hi])
            if dst[0] == 'mem_named' and src[0] == 'reg':
                lo, hi = self._mem_bytes(dst, label_addr)
                return bytes([0xF6, 0xF0 | src[1], lo, hi])
            if dst[0] == 'reg' and src[0] == 'imm_addr':
                # reg <- endereço da variável (não seu conteúdo) - mesma
                # forma de imediato largo "E6 Fn ll hh" já usada pra
                # MOV Rw,#data16, só que o valor vem de resolve_mem_addr
                # em vez de um literal já conhecido na passagem 1.
                addr = self.resolve_mem_addr(src[1], 0, label_addr)
                return bytes([0xE6, 0xF0 | dst[1], addr & 0xFF, (addr >> 8) & 0xFF])
            if dst[0] == 'reg' and src[0] == 'ind':
                # "A8 nm" (plain) / "98 nm" (pós-incrementa Rm) - MOV Rn,[Rm(+)]
                m, mode = src[1], src[2]
                op = 0x98 if mode == 'postinc' else 0xA8
                assert mode in ('plain', 'postinc'), "MOV Rn,[Rm] não suporta pré-decremento como origem"
                return bytes([op, (dst[1] << 4) | m])
            if dst[0] == 'ind' and src[0] == 'reg':
                # "B8 nm" (plain) / "88 nm" (pré-decrementa Rm) - MOV [Rm(+-)],Rn
                # convenção: nibble alto = n (valor/Rn), baixo = m (ponteiro/Rm)
                m, mode = dst[1], dst[2]
                op = 0x88 if mode == 'predec' else 0xB8
                assert mode in ('plain', 'predec'), "MOV [Rm],Rn não suporta pós-incremento como destino"
                return bytes([op, (src[1] << 4) | m])
            if dst[0] == 'reg' and src[0] == 'ind_off':
                # "D4 nm ## ##" - MOV Rn,[Rm+#data16] (ABI de pilha do c167cc)
                m, disp = src[1], src[2] & 0xFFFF
                return bytes([0xD4, (dst[1] << 4) | m, disp & 0xFF, (disp >> 8) & 0xFF])
            if dst[0] == 'ind_off' and src[0] == 'reg':
                # "C4 nm ## ##" - MOV [Rm+#data16],Rn
                m, disp = dst[1], dst[2] & 0xFFFF
                return bytes([0xC4, (src[1] << 4) | m, disp & 0xFF, (disp >> 8) & 0xFF])
            raise AssertionError(f"forma de MOV não codificável: {dst} <- {src}")

        if mnemonic == 'ADD':
            dst, src = operands
            assert dst[0] == 'reg'
            if src[0] == 'reg':
                return bytes([0x00, (dst[1] << 4) | src[1]])
            lo, hi = src[1] & 0xFF, (src[1] >> 8) & 0xFF
            return bytes([0x06, 0xF0 | dst[1], lo, hi])

        if mnemonic == 'SUB':
            dst, src = operands
            assert dst[0] == 'reg'
            if src[0] == 'reg':
                return bytes([0x20, (dst[1] << 4) | src[1]])
            lo, hi = src[1] & 0xFF, (src[1] >> 8) & 0xFF
            return bytes([0x26, 0xF0 | dst[1], lo, hi])

        if mnemonic == 'CMP':
            dst, src = operands
            assert dst[0] == 'reg'
            if src[0] == 'reg':
                return bytes([0x40, (dst[1] << 4) | src[1]])
            if src[0] == 'imm':
                lo, hi = src[1] & 0xFF, (src[1] >> 8) & 0xFF
                return bytes([0x46, 0xF0 | dst[1], lo, hi])
            lo, hi = self._mem_bytes(src, label_addr)
            return bytes([0x42, 0xF0 | dst[1], lo, hi])

        if mnemonic in ('MUL', 'MULU'):
            op1, op2 = operands
            assert op1[0] == 'reg' and op2[0] == 'reg'
            opcode = 0x0B if mnemonic == 'MUL' else 0x1B
            return bytes([opcode, (op1[1] << 4) | op2[1]])

        if mnemonic in ('SHR', 'ASHR', 'SHL'):
            dst, src = operands
            assert dst[0] == 'reg'
            rr_op, ri_op = {'SHR': (0x6C, 0x7C), 'ASHR': (0xAC, 0xBC), 'SHL': (0x4C, 0x5C)}[mnemonic]
            if src[0] == 'reg':
                return bytes([rr_op, (dst[1] << 4) | src[1]])
            assert 0 <= src[1] <= 15
            # "7C #n" etc: nibble alto = imediato (quantidade de shift),
            # baixo = registrador - mesma correção do MOV Rw,#data4 acima
            return bytes([ri_op, (src[1] << 4) | dst[1]])

        if mnemonic in ('OR', 'AND', 'XOR', 'ADDC', 'SUBC'):
            dst, src = operands
            assert dst[0] == 'reg'
            if src[0] == 'reg':
                opcode = {'OR': 0x70, 'AND': 0x60, 'XOR': 0x50, 'ADDC': 0x10, 'SUBC': 0x30}[mnemonic]
                return bytes([opcode, (dst[1] << 4) | src[1]])
            # "Rw,#data16" (opcode 0x76/0x66/0x56, ALU_REGD16 em c166sim.py) -
            # achado 21/08/2026 corrigindo o compilador: zero-extensão de
            # byte carregado (`AND Rw,#0x00FF`) precisa disso pra qualquer
            # registrador de destino, não só R0-R7 (que têm alias de byte
            # RLn/RHn - os outros, R8-R10 do pool de temporários e R11/R12
            # de rascunho de spill, não têm).
            assert src[0] == 'imm', f"{mnemonic} só aceita reg,reg ou reg,#imm16"
            opcode = {'OR': 0x76, 'AND': 0x66, 'XOR': 0x56}[mnemonic]
            lo, hi = src[1] & 0xFF, (src[1] >> 8) & 0xFF
            return bytes([opcode, 0xF0 | dst[1], lo, hi])

        raise AssertionError(mnemonic)


def main():
    if len(sys.argv) != 3:
        print("uso: c166asm.py <entrada.asm> <saida.bin>", file=sys.stderr)
        sys.exit(1)
    src, dst = sys.argv[1], sys.argv[2]

    a = Asm()
    a.load_file(src)
    binimg, labels, varaddrs, code_end, data_end = a.assemble(org=0)

    with open(dst, 'wb') as f:
        f.write(binimg)

    print(f"{src} -> {dst}: {len(binimg)} bytes")
    print(f"  código: 0x0000-0x{code_end-1:04X} ({code_end} bytes)")
    print(f"  dados : 0x{code_end:04X}-0x{data_end-1:04X}")
    print("  labels:", ', '.join(f"{k}=0x{v:04X}" for k, v in sorted(labels.items(), key=lambda kv: kv[1])))
    print("  vars  :", ', '.join(f"{k}=0x{v:04X}" for k, v in sorted(varaddrs.items(), key=lambda kv: kv[1])))


if __name__ == '__main__':
    main()
