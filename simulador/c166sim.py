#!/usr/bin/env python3
"""Simulador mínimo de C166/C167 - só o subconjunto de opcodes usado pelos
.bin gerados por c166asm.py (MOV, ADD, SUB, CMP, MUL, DIV, SHR, NEG, JMPR,
NOP). Não é um simulador de propósito geral: serve pra conferir que o
código montado por c166asm.py faz o que o .asm pretende.

Memória: uma imagem plana única (o próprio .bin), lida/escrita por endereço
de byte, little-endian pra words. MDL/MDH ficam nos endereços reais
0xFE0C/0xFE0E - como esses ficam fora da região código+dados do .bin, a
imagem é alocada com 0x10000 bytes (64K) e o .bin é carregado no início.

Uso:
    python3 c166sim.py fall.bin Y0=25600 V0=0 G=2508 DT=25 E=204
    python3 c166sim.py fall.bin --steps=300 --syms=fall.asm Y0=25600 ...

Roda direto o .bin (não monta nada) - passe --syms=arquivo.asm só como
conveniência opcional pra poder usar nomes de variável em vez de endereço
hex cru nos inputs/saída (chama o c166asm.py apenas para extrair a tabela
de símbolos, sem tocar no .bin já existente). Sem --syms, os inputs/saída
usam endereço hex diretamente (ex.: 0x2A=5).
"""
import sys
import re

MDL_ADDR = 0xFE0C
MDH_ADDR = 0xFE0E
SP_ADDR = 0xFE12  # SFR real do C166/C167 - mesmo endereço usado pelo boot real
                  # da Copa Clio ("MOV 0xFE12,#0xFC16"), confirmado no c166dis.py
DEFAULT_SP = 0xFC00  # pilha cresce pra baixo a partir daqui se o programa não
                      # definir SP explicitamente (MOV 0xFE12,#...) - só um
                      # default seguro acima de qualquer código/dado dos
                      # programas de exemplo, não é um valor "oficial"
MAX_STEPS = 200000


class Trap(Exception):
    pass


class _RegisterBank:
    """GPRs r0-r15 como janela na IRAM em CP+2*n (manual §3.2) - não uma lista de
    registradores de CPU fixos. Ver comentário em Sim.__init__ pra motivação
    completa (achado 19/08/2026: corrupção de registrador entre tarefas via
    SCXT, porque a implementação antiga era uma lista Python solta). Delega pra
    `sim.mem` via `sim.w16`/`sim.set_w16`, lendo CP (0xFE10) a cada acesso -
    fica sempre coerente mesmo se CP mudar no meio da execução (SCXT)."""
    __slots__ = ('sim',)

    def __init__(self, sim):
        self.sim = sim

    def __getitem__(self, n):
        # inline de w16() (achado com cProfile: acesso a registrador é o
        # caminho mais quente do simulador inteiro - quase 1 chamada por
        # instrução - e cada chamada extra de método custa; comportamento
        # IDÊNTICO a `self.sim.w16(0xFE10)`/`self.sim.w16(cp+2*n)`, só sem a
        # camada de função a mais) - otimização de desempenho, 20/08/2026.
        mem = self.sim.mem
        cp = mem[0xFE10] | (mem[0xFE11] << 8)
        addr = (cp + 2 * n) & 0xFFFF
        return mem[addr] | (mem[addr + 1] << 8)

    def __setitem__(self, n, val):
        mem = self.sim.mem
        cp = mem[0xFE10] | (mem[0xFE11] << 8)
        addr = (cp + 2 * n) & 0xFFFF
        val &= 0xFFFF
        mem[addr] = val & 0xFF
        mem[addr + 1] = (val >> 8) & 0xFF


class Sim:
    # ADD/SUB/XOR/AND/OR reg<->mem e reg,#data16 (formas de 4 bytes com
    # endereço/imediato cheio, sem ambiguidade de nibble - o "reg" aqui é
    # sempre o campo compacto de read_regfield16/write_regfield16). SUB
    # regmem/memreg não aparecem no boot real até agora, mas fica pronto.
    ALU_REGMEM = {0x02: 'ADD', 0x22: 'SUB', 0x52: 'XOR', 0x62: 'AND', 0x72: 'OR'}
    ALU_MEMREG = {0x04: 'ADD', 0x24: 'SUB', 0x54: 'XOR', 0x64: 'AND', 0x74: 'OR'}
    ALU_REGD16 = {0x06: 'ADD', 0x56: 'XOR', 0x66: 'AND', 0x76: 'OR'}

    # ADDB/ADDCB/SUBB/SUBCB/CMPB/XORB/ANDB/ORB reg,#data8 ("XX RR ## xx" -
    # mesmo layout do MOVB reg,#data8/0xE7 já implementado).
    ALU_BYTE_REGD8 = {0x07: 'ADD', 0x17: 'ADDC', 0x27: 'SUB', 0x37: 'SUBC',
                       0x47: 'CMP', 0x57: 'XOR', 0x67: 'AND', 0x77: 'OR'}

    # forma curta "ind" (2 bytes, kind 'ind' em c166dis.py, desambiguada lá em
    # 15/08/2026): byte2 nibble alto = registrador destino, nibble baixo com
    # bit3=0 -> Rw,#data3 (valor imediato 0-7); bit3=1,bit2=0 -> Rw,[Ri];
    # bit3=1,bit2=1 -> Rw,[Ri+] (pós-incrementa Ri) - "ii" só endereça R0-R3.
    IND_WORD_OPS = {0x08: 'ADD', 0x18: 'ADDC', 0x28: 'SUB', 0x38: 'SUBC',
                    0x48: 'CMP', 0x58: 'XOR', 0x68: 'AND', 0x78: 'OR'}
    IND_BYTE_OPS = {0x09: 'ADD', 0x19: 'ADDC', 0x29: 'SUB', 0x39: 'SUBC',
                     0x49: 'CMP', 0x59: 'XOR', 0x69: 'AND', 0x79: 'OR'}

    # BCLR/BSET bitoff.N: opcode "qE"/"qF" - nibble alto do PRÓPRIO opcode é
    # a posição do bit (0-15), byte2 é o bitoff (mesma tabela do JB/JNB/etc.
    # ver bitoff_addr()). Mesmo padrão de geração do c166dis.py.
    BCLR_OPS = {base: (base >> 4) & 0xF for base in range(0x0E, 0x100, 0x10)}
    BSET_OPS = {base + 1: (base >> 4) & 0xF for base in range(0x0E, 0x100, 0x10)}

    def _alu_op(self, name, a, b):
        if name == 'ADD':
            res = a + b
            self.update_flags_add(a, b, res)
            return res & 0xFFFF
        if name == 'SUB':
            res = a - b
            self.update_flags_sub(a, b, res)
            return res & 0xFFFF
        if name == 'AND':
            res = a & b
        elif name == 'OR':
            res = a | b
        else:  # XOR
            res = a ^ b
        self.flags['Z'] = res == 0
        return res

    def _mov_flags16(self, val):
        """MOV (word) ATUALIZA Z/N com base no valor movido - achado 19/08/2026
        investigando o dispatcher de diagnóstico real (master_diag_dispatcher,
        file 0x1A2FA): "MOV r4,0xF7F0" seguido de "JMPR cc_Z" testa a sessão
        lendo a flag que o PRÓPRIO MOV acabou de setar - não é resíduo de
        instrução anterior. Confirmado no manual: "MOV ... Z=Set se op2==0,
        N=Set se MSB de op2 setado" (V/C não afetados). Sem isso nenhum código
        real que usa esse idiomatismo (muito comum) funcionaria certo."""
        self.flags['Z'] = val == 0
        self.flags['N'] = (val & 0x8000) != 0

    def _mov_flags8(self, val):
        self.flags['Z'] = (val & 0xFF) == 0
        self.flags['N'] = (val & 0x80) != 0

    def _enter_trap(self, vector, ret_ip, csp):
        """Empilha PSW, CSP, IP e desvia pro endereço de vetor - mecanismo comum
        a TRAP (software, opcode 0x9B) e interrupção de hardware (ex.: CC26/T7-T8,
        ver _check_hw_timer_interrupt). Fatorado 19/08/2026 quando a interrupção de
        hardware foi implementada, pra não duplicar a lógica de push já validada
        no TRAP."""
        psw = ((1 if self.flags['N'] else 0)
               | (2 if self.flags['C'] else 0)
               | (4 if self.flags['V'] else 0)
               | (8 if self.flags['Z'] else 0))
        sp = self.get_special(SP_ADDR)
        sp = (sp - 2) & 0xFFFF
        self.set_w16(sp, psw)
        sp = (sp - 2) & 0xFFFF
        self.set_w16(sp, csp)
        sp = (sp - 2) & 0xFFFF
        self.set_w16(sp, ret_ip)
        self.set_special(SP_ADDR, sp)
        self.pc = vector

    # Interrupção de hardware de verdade - CAPCOM Register 26 (CC26, SFR 0xFE74,
    # trap number 0x3A/58, vetor 0x00E8 - manual Tabela 5-1). Achado 19/08/2026
    # rastreando o handler em `file 0x16048` (prólogo/epílogo `SCXT 0xFE10,.../POP
    # 0xFE10;RETI` = convenção de interrupção real do projeto, `notas_desmontagem.md`
    # §0b2): o handler faz `ADD 0xFE74,#0x00FA` (recarrega CC26 += 250 a cada
    # disparo, padrão clássico de "compare register" auto-reload) e mexe num
    # semáforo `0xFD9A.15` que uma tarefa em `file 0x1805C` fica esperando via
    # `JNB` - ou seja, é o "tick" periódico que várias tarefas dependem. NÃO existe
    # nenhuma instrução `TRAP #0x3A` (`9B 3A`) no firmware inteiro (grep confirmou
    # 0 ocorrências) - só pode ser alcançado por interrupção de hardware de
    # verdade, não por software, então tem que ser modelado como tal.
    #
    # Simplificação DELIBERADA (documentada, não uma implementação completa de
    # CAPCOM2/T7/T8): o C167CR real gera esse interrupt comparando CC26 contra um
    # dos timers T7/T8 de 16 bits (modo/seleção configurados em T78CON/CCM4-7, não
    # rastreados). Em vez de emular o par de timers T7/T8 e toda a lógica de modo
    # de comparação, modelamos só o EFEITO observável: um contador de hardware
    # sintético (`self._hw_timer`) que avança 1 tick por instrução executada (não
    # é ciclo-a-ciclo preciso, mas não precisa ser pra desbloquear a exploração) e
    # dispara a interrupção por IGUALDADE de 16 bits contra CC26 - mesma semântica
    # de "compare match" de hardware, só a fonte do tick que é aproximada. IEN/ILVL
    # (prioridade/máscara global de interrupção) também não são modelados - a
    # interrupção é sempre considerada habilitada, mesma simplificação já aceita
    # pro TRAP (PSW só carrega N/C/V/Z, não ILVL/IEN).
    CC26_ADDR = 0xFE74
    CC26_VECTOR = 0xE8

    def _check_hw_timer_interrupt(self):
        self._hw_timer = (self._hw_timer + 1) & 0xFFFF
        # guarda de não-reentrância: hardware real não deixaria essa interrupção se
        # preemptar (mesma prioridade/ILVL, que não modelamos) - achado 19/08/2026
        # rodando contra firmware real: sem essa guarda, o handler (que faz um loop
        # de espera curto lendo T1 - `file 0x179E0`) era reinterrompido por ele mesmo
        # a cada ~250 passos antes de terminar, reiniciando o baseline do loop pra
        # sempre (nunca convergia). `self._hw_irq_sp_watermark` guarda o SP de ANTES
        # do push; só permite disparar de novo depois que o RETI correspondente
        # desempilhar de volta até esse nível (ou acima).
        #
        # ORDEM dos 2 guards abaixo trocada pra ANTES da leitura de CC26
        # (otimização de desempenho, 20/08/2026, comportamento idêntico -
        # eram checados DEPOIS de já ter pago o custo de w16(CC26_ADDR) sem
        # necessidade, já que os dois só servem pra abortar de qualquer jeito):
        # evita 1 leitura de memória + chamada de método a cada instrução
        # enquanto uma IRQ já está em andamento ou uma janela EXTP/EXTS/EXTR/
        # ATOMIC está ativa - os dois casos são comuns o bastante (toda
        # instrução paginada usa EXTP) pra valer a pena adiar a leitura.
        if self._hw_irq_sp_watermark is not None:
            return
        if self.ext_active is not None:
            return
        # cc26 != 0 exclui o valor de reset (0x0000, manual): enquanto o firmware
        # nunca programou um alvo real, o hardware também não dispararia (CCM4-7/IEN
        # também ficam em reset = desabilitados) - sem essa guarda, um programa de
        # teste pequeno (sem tabela de vetores/init de verdade) dispara um "match"
        # espúrio quando o contador sintético dá a volta em 0 de novo (achado rodando
        # a regressão de fall.asm: virou opcode inválido em pc=0xFBFA, vetor 0xE8
        # apontando pra área sem código real).
        #
        # manual: "Instructions EXTP and EXTS inhibit interrupts the same way as
        # ATOMIC" - achado 19/08/2026 rastreando corrupção real de DPP3 (0xFE06):
        # sem essa guarda (`self.ext_active is not None`, ver acima), a
        # interrupção podia disparar NO MEIO de uma janela EXTP/EXTS/EXTR/ATOMIC
        # de outra instrução (que dura N instruções), rodar o handler (que
        # também usa endereçamento paginado), e ao voltar via RETI a janela
        # original já tinha sido consumida errado (`self.ext_active` é global,
        # não salvo/restaurado por interrupção) - próxima instrução do código
        # interrompido perdia a página que esperava, escrevendo endereço errado
        # em DPP3.
        cc26 = self.w16(self.CC26_ADDR)
        if cc26 != 0 and self._hw_timer == cc26:
            self._hw_irq_sp_watermark = self.get_special(SP_ADDR)
            self._enter_trap(self.CC26_VECTOR, self.pc & 0xFFFF, (self.pc >> 16) & 0xFF)

    # ISR real em `file 0x16DA8` (físico 0x116DA8, confirmada por `SCXT`/
    # `RETI` genuínos no código) que limpa `0xFD9C.9` - bit que, uma vez
    # setado por `file 0x27D06` e nunca limpo, faz o firmware cair de
    # propósito no vetor não atribuído `0x44`/CAPCOM29 (self-loop
    # `0x110320`, ver simulador/README.md e notas/notas_desmontagem.md §0e).
    # NÃO SABEMOS o evento de hardware real que dispara essa ISR (não bate
    # com nenhum dos 8 vetores reais com handler distinto na tabela de 128 -
    # achado escaneando a tabela inteira) - mesma simplificação pragmática
    # já aceita pro CC26 acima: em vez de modelar o periférico desconhecido
    # por trás, disparamos o CÓDIGO REAL da ISR (não um atalho que só limpa
    # o bit por fora) um tempo sintético depois do bit ser setado, deixando
    # a própria lógica da ISR decidir o que fazer (ela mesma lê o bit e
    # decide limpar `.9` ou `.11`, ver `CMP r0,#0x46A0`). Retratado
    # 20/08/2026: essa ISR NÃO é EEPROM (usa `EXTR_ATOMIC`/SFR estendido,
    # não `EXTP`/`EXTS` de paginação - a hipótese anterior de EEPROM estava
    # errada, ver notas_desmontagem.md §16, já fechada com "zero evidência
    # de escrita de EEPROM" por auditoria exaustiva independente).
    PENDING_OP_BIT_ADDR = 0xFD9D   # byte ALTO do word 0xFD9C - bit 9 do word cai no bit 1 daqui
    PENDING_OP_BIT_MASK = 0x02
    PENDING_OP_ISR_TARGET = 0x116DA8  # físico (file 0x16DA8 + 0x100000, mesma convenção de sempre)
    PENDING_OP_DELAY = 50  # instruções sintéticas de "latência" antes da ISR disparar

    def _check_pending_op_isr(self):
        if self._pending_op_deadline is not None:
            if self._cycle < self._pending_op_deadline:
                return
            self._pending_op_deadline = None
            if self._hw_irq_sp_watermark is not None or self.ext_active is not None:
                return  # mesmas guardas do CC26 acima - tenta de novo no próximo step
            self._hw_irq_sp_watermark = self.get_special(SP_ADDR)
            # a ISR faz `SCXT 0xFE10,#0xFAEA` como 1ª instrução (troca de
            # banco de registrador) e logo depois `CMP r0,#0x46A0; JMPR
            # cc_C,0x16DE2` - cc_C aqui é ULT (menor-que sem sinal): com
            # r0=0 (default de RAM zerada) o desvio tomado é `MOV
            # r1/r0,[RwindRw]; CALLI cc_UC,[r1]` (indireto por um ponteiro
            # que não temos motivo pra achar válido - foi isso que mandou
            # a exploração pra memória aleatória na 1ª tentativa, achado
            # testando). Pré-carregando r0 (= mem[0xFAEA], o R0 do banco
            # que o SCXT vai ativar) com >= 0x46A0 força o OUTRO caminho
            # (limpar as flags e seguir, sem call indireto) - sem isso não
            # tem como saber que ponteiro real iria nesse registrador.
            self.set_w16(0xFAEA, 0x46A0)
            self._enter_trap(self.PENDING_OP_ISR_TARGET, self.pc & 0xFFFF, (self.pc >> 16) & 0xFF)
            return
        if self.mem[self.PENDING_OP_BIT_ADDR] & self.PENDING_OP_BIT_MASK:
            self._pending_op_deadline = self._cycle + self.PENDING_OP_DELAY

    # HACK pragmático (20/08/2026, ponte OBD2/K-line) - mesmo molde do
    # PENDING_OP_ISR acima: o simulador não tem despachante genérico de
    # interrupção (IEN/PRIOR->vetor), só estes gatilhos sintéticos
    # especiais. `uart_inject_rx_byte()` (ver abaixo) só seta o byte em
    # S0RBUF e a flag S0RIR (`0xFF6E.7`) - sem isso, a ISR real do ASC0
    # (`isr_asc0_receive`, file 0x3406, vetor 43) nunca roda e o firmware
    # nunca "vê" o byte injetado (`0xFC5C` fica sempre 0, achado testando
    # a ponte). Dispara o CÓDIGO REAL da ISR (não um atalho) um tempo
    # sintético depois da flag ser setada, deixando a lógica dela decidir
    # o que fazer com o byte.
    ASC0_RX_ISR_TARGET = 0x103406  # físico (file 0x3406 + 0x100000)
    ASC0_RX_ISR_DELAY = 20  # instruções sintéticas de "latência" antes da ISR disparar

    def _check_asc0_rx_isr(self):
        if self._asc0_rx_deadline is not None:
            if self._cycle < self._asc0_rx_deadline:
                return
            if not (self.w16(self.UART_RIC_ADDR) & 0x80):
                # achado 20/08/2026: sem esta checagem, um retry adiado (ver
                # comentário abaixo) podia disparar a ISR DE NOVO depois que
                # a 1ª execução já tinha limpo a flag sozinha (`BCLR
                # 0xFF6E.7`, 1ª instrução real da ISR) - 2 disparos pro
                # MESMO byte, o 2º lendo `S0RBUF` já sobrescrito pelo
                # próximo `uart_inject_rx_byte()` (achado testando a ponte
                # OBD2: registrador de deslocamento do decodificador de
                # cabeçalho, `file 0x3474`, sempre com os 2 últimos bytes
                # iguais)
                self._asc0_rx_deadline = None
                return
            if self._hw_irq_sp_watermark is not None or self.ext_active is not None:
                # janela ocupada (CC26 ainda "em voo", ou EXTP/EXTS/ATOMIC ativo) -
                # tenta de novo no PRÓXIMO step, não daqui a 20 - achado
                # empiricamente: com retry a cada 20 passos, a janela livre
                # (só ~0.7% dos passos, CC26 dispara com frequência) quase
                # nunca coincidia com o momento exato do teste, ISR nunca disparava
                self._asc0_rx_deadline = self._cycle + 1
                return
            self._asc0_rx_deadline = None
            self._hw_irq_sp_watermark = self.get_special(SP_ADDR)
            self._enter_trap(self.ASC0_RX_ISR_TARGET, self.pc & 0xFFFF, (self.pc >> 16) & 0xFF)
            return
        if self.w16(self.UART_RIC_ADDR) & 0x80:
            self._asc0_rx_deadline = self._cycle + self.ASC0_RX_ISR_DELAY

    def _div_by_zero(self):
        # Manual (c167cr_userguide.pdf, p.2-45): "a division by zero will
        # always cause an overflow" - NAO ha trap de hardware documentado
        # para isso (ao contrario do que se pensava antes - ver README.md).
        # A CPU real so seta V=1/C=0 e segue em frente; o conteudo exato de
        # MDL/MDH apos isso nao e especificado no manual (resultado do
        # algoritmo shift-subtract com divisor 0), entao deixamos MDL/MDH
        # como estavam (nao ha valor "correto" documentado pra reproduzir).
        self.flags['V'] = True
        self.flags['C'] = False

    def _shift(self, val, count, kind):
        """SHL/SHR/ASHR bit-a-bit, com as flags exatas do manual Infineon -
        ACHADO 19/08/2026 rodando o boot real: a implementação anterior não
        tocava a flag C nenhuma, e o march test de RAM da Copa Clio testa
        `JMPR cc_NC` logo depois de um SHL pra saber quando parar de deslocar
        um bit-caminhante - sem C setada, o loop nunca termina (travou o
        simulador num loop infinito real, só descoberto rodando contra
        firmware de verdade, não pelos programas de exemplo). SHL: C = MSB
        que saiu por cima a cada passo. SHR/ASHR: C = LSB que saiu por baixo;
        V = OR de todos os bits que passaram pelo C durante o loop (flag de
        arredondamento, não overflow de verdade). ASHR preserva o sinal
        original ao preencher os bits altos."""
        val &= 0xFFFF
        c = False
        v = False
        if kind == 'SHL':
            for _ in range(count):
                c = (val & 0x8000) != 0
                val = (val << 1) & 0xFFFF
        else:
            sign = val & 0x8000 if kind == 'ASHR' else 0
            for _ in range(count):
                c = (val & 1) != 0
                v = v or c
                val = (val >> 1) | sign
        self.flags['Z'] = val == 0
        self.flags['N'] = (val & 0x8000) != 0
        self.flags['V'] = v
        self.flags['C'] = c if count > 0 else False
        return val

    def _rotate(self, val, count, kind):
        """ROL/ROR bit-a-bit (manual §ROL/ROR, "Detailed Description"): ROL
        gira bit 15 pra dentro do bit 0 E do Carry a cada passo (V sempre
        0); ROR gira bit 0 pra dentro do bit 15 E do Carry (V = OR de todos
        os bits que passaram pelo Carry durante o giro, igual ao SHR/ASHR em
        `_shift`). C sempre reflete o último bit girado, e fica limpo (não
        preservado) se count==0 - mesma convenção já usada em `_shift`."""
        val &= 0xFFFF
        c = False
        v = False
        if kind == 'ROL':
            for _ in range(count):
                c = (val & 0x8000) != 0
                val = ((val << 1) | (1 if c else 0)) & 0xFFFF
        else:  # ROR
            for _ in range(count):
                c = (val & 1) != 0
                v = v or c
                val = (val >> 1) | (0x8000 if c else 0)
        self.flags['Z'] = val == 0
        self.flags['N'] = (val & 0x8000) != 0
        self.flags['V'] = v if kind == 'ROR' else False
        self.flags['C'] = c if count > 0 else False
        return val

    def __init__(self, image):
        # 16MB (24 bits, seg 0x00-0xFF) - não só 64KB - pra JMPS/CALLS/JMPA/
        # JMPI/CALLA/CALLI poderem alcançar endereço fora do segmento 0. `pc`
        # é sempre o endereço físico completo (seg*0x10000+offset); o
        # segmento "atual" nunca é guardado à parte, é sempre `pc & 0xFF0000`
        # - válido porque só trocamos de segmento via JMPS/CALLS, que setam
        # pc explicitamente. JMPR/CALLR continuam com aritmética relativa de
        # 16 bits só sobre os 2 bytes do operando, então em teoria não
        # cruzam borda de segmento sozinhos (limitação aceita: se cruzassem,
        # divergiria do hardware real, que só soma no IP de 16 bits).
        self.mem = bytearray(0x1000000)
        self.mem[:len(image)] = image
        # ESPELHADO em +0x100000: a convenção já estabelecida no projeto
        # inteiro (ferramentas_disassembly/README.md: "endereço da CPU =
        # offset do arquivo + 0x100000") - achado 19/08/2026 rastreando uma
        # chamada sintética pro dispatcher de diagnóstico: "CALLS
        # seg=0x11,0xB02C" (físico 0x11B02C) apontava pra memória zerada, já
        # que só tínhamos carregado a imagem em offset físico 0. Provável
        # explicação: o boot roda inicialmente com o flash mapeado numa
        # janela de chip-select baixa (por isso o vetor de reset em seg=0x00
        # funciona sem ajuste - é a MESMA memória, só vista pelo endereço
        # baixo antes do POST reprogramar SYSCON/ADDRSEL/BUSCON), e depois da
        # reconfiguração de bus o mesmo flash físico passa a responder em
        # +0x100000 também - por isso a maioria do código (CALLS/JMPS seg
        # != 0x00, virtualmente tudo fora do vetor de reset) usa esse offset.
        # Espelhar nos dois endereços cobre ambas as janelas sem precisar
        # decidir qual instrução roda "antes" ou "depois" do remapeamento.
        if len(image) <= 0x100000:
            self.mem[0x100000:0x100000 + len(image)] = image
        # RAM interna/SFR/ESFR de verdade (0xF000-0xFFFF) fica ESPELHADA por
        # engano com bytes crus do .bin acima (o .bin é um dump de FLASH, não
        # inclui RAM) - limpa essa janela pra começar em branco como no reset
        # real, ANTES de setar SP/DPP abaixo (senão apagaria eles também).
        for a in range(0xF000, 0x10000):
            self.mem[a] = 0
        self._sfr_mock = {}   # endereço -> (valor_na_ultima_escrita, ciclo_na_ultima_escrita)
        self._cycle = 0       # incrementado 1x por instrução em step() (não em _step_inner)
        self._hw_timer = 0    # tick sintético da interrupção CC26 - ver _check_hw_timer_interrupt
        self._hw_irq_sp_watermark = None  # guarda de não-reentrância - ver _check_hw_timer_interrupt
        self._pending_op_deadline = None  # ver _check_pending_op_isr
        self._asc0_rx_deadline = None  # ver _check_asc0_rx_isr
        self.uart_tx_queue = []  # bytes que a ECU escreveu em S0TBUF, esperando ser lidos - ver uart_pop_tx_bytes()
        # Watchdog Timer (WDT) - manual §13. Registrador WDT de 16 bits, conta de
        # 0x0000 até estourar em 0x10000 (overflow = reset interno de verdade).
        # `SRVWDT` recarrega pro valor `WDTREL<<8` (WDTREL = byte alto de WDTCON,
        # 0xFFAE). ACHADO 20/08/2026 investigando por que `rotina_subsistema_init_com_trap`
        # é incondicional (ver notas/TRACE_BOOT.md): o self-loop de
        # `vetor_capcom29_unassigned` (`0x110320`) nunca alimenta o watchdog -
        # hipótese de que hardware real reseta sozinho ao ficar preso ali, em vez
        # de travar pra sempre. Implementado pra testar essa hipótese diretamente.
        # Simplificação: 1 tick de WDT por instrução executada (mesma convenção já
        # aceita pro CC26/T1 - não é cycle-accurate aos ~0,96ms reais do manual pra
        # WDTREL típico, mas preserva o comportamento qualitativo: se SRVWDT não for
        # chamado com frequência suficiente em contagem de instruções, reseta).
        self._wdt = 0
        self._wdt_tick_accum = 0  # ver WDT_TICK_NUMERATOR/DENOMINATOR acima
        self._wdt_reload = 0  # WDTREL (byte alto de WDTCON) - 0 até o firmware programar
        self.wdt_reset_count = 0  # contador de resets por watchdog (diagnóstico/teste)
        # DESLIGADO por padrão (diferente do pré-arme do CC26, que liga
        # sozinho pra imagem com vetor real) - achado testando contra
        # firmware real 20/08/2026: o march test de RAM do POST
        # (`file 0x1B44-0x1BDE`) só chama SRVWDT 1x no início e se compromete
        # a terminar sem realimentar de novo; com a aproximação "1 tick por
        # instrução" (não cycle-accurate, C166 é pipelined, não dá pra
        # calibrar direito sem hardware real) isso bate no orçamento do
        # watchdog por ~99 passos e reseta ANTES de qualquer exploração
        # normal completar o boot - ver notas/TRACE_BOOT.md pra ressalva
        # completa. Ligar manualmente (`sim._wdt_enabled = True`) só quando
        # for testar hipótese de watchdog especificamente (ex.: confirmar
        # que o self-loop de vetor não atribuído reseta em vez de travar
        # pra sempre - já validado assim, ver TRACE_BOOT.md).
        self._wdt_enabled = False
        # PRÉ-ARMADO (simplificação pragmática, documentada em _check_hw_timer_interrupt):
        # a rotina real que arma CC26 pela primeira vez (`file 0x3B0AA`, "MOV
        # 0xFE74, r4" com r4 = T1+0xFA) fica atrás de um `CALLS` que a exploração
        # atual não alcança antes de travar esperando o próprio resultado dessa
        # interrupção (deadlock: o wait em `file 0x1805C` só sai se CC26 já tiver
        # sido armado, mas armar CC26 depende de código que só roda depois desse
        # wait na ordem observada). Em vez de rastrear por que o call site real não
        # é alcançado, assume-se que a inicialização já rodou (mesmo efeito de uma
        # sessão anterior de exploração ter chegado lá) - replica o valor real:
        # próximo alvo = "T1 agora" + 250 (mesmo período usado pelo próprio handler
        # em `file 0x16048` a cada recarga).
        #
        # SÓ pra imagem com tabela de vetores real (byte 0 = 0xFA, opcode
        # JMPS - reset vector de verdade). ACHADO 20/08/2026, corrigindo a
        # heurística de fim-de-programa (ver Sim.run()): programas de teste
        # montados por c166asm.py (fat.asm/filter.asm) não têm vetor 0xE8
        # nenhum carregado (região fica com bytes 0 crus, ou colide com a
        # pilha) - antes disso passava despercebido porque o halt-por-NOP
        # antigo parava a simulação bem antes dos ~250 passos necessários pro
        # disparo; com o halt por loop auto-referente (que precisa de vários
        # steps pra confirmar), o mock chegava a disparar pra esses programas
        # pequenos também, desviando `pc` pra memória sem código real e
        # travando com opcode inválido (`pc=0xFBFA`, já flagrado antes no
        # comentário de `_check_hw_timer_interrupt` rodando fall.asm). Real
        # hardware também não dispararia aqui: CC26 só é armado por firmware
        # de verdade, nunca em programa sintético sem init nenhum.
        if len(image) >= 1 and image[0] == 0xFA:
            self.set_w16(0xFE74, 250)

        # CP (Context Pointer, SFR 0xFE10) - reset FC00H (manual). ACHADO 19/08/2026
        # rastreando corrupção de r2 num teste do POST: `self.r` era uma lista Python
        # solta, sem NENHUMA ligação com o CP - só funcionava por coincidência
        # enquanto nenhum código real trocava de banco. No C166 real os GPRs NÃO são
        # registradores de CPU fixos: são uma "janela" de até 16 words na IRAM,
        # começando no endereço apontado por CP (manual §3.2, "Context Pointer (CP)
        # register determines the base address of the currently active register
        # bank"). `SCXT 0xFE10,#novo_banco` (usado em TODO handler de
        # interrupção/trap deste firmware, inclusive TRAP #0x7E disparado durante o
        # POST) troca CP pra dar a cada tarefa/handler seu PRÓPRIO banco de
        # registradores, sem precisar salvar/restaurar cada Rn manualmente. Como
        # `self.r` era uma lista fixa, TODA troca de CP era um no-op silencioso -
        # toda tarefa continuava lendo/escrevendo o MESMO r0-r15 de sempre,
        # vazando valores de um contexto pro outro (raiz real da corrupção de
        # `r2`/DPP3 encontrada explorando o boot). Substituído por `_RegisterBank`,
        # que redireciona `self.r[n]` pra memória de verdade em `CP+2*n` - todo o
        # resto do simulador continua usando `self.r[i]`/`self.r[i]=val` sem
        # nenhuma mudança de código, já que a classe só sobrescreve
        # `__getitem__`/`__setitem__`.
        self.r = _RegisterBank(self)
        self._reset_cpu_registers()

    def _reset_cpu_registers(self):
        """CP/SP/DPP0-3/flags/pc/ext_active pro valor de RESET do manual -
        fatorado do `__init__` em 20/08/2026 pra ser reusável por
        `_watchdog_reset()` (overflow do WDT = reset interno de verdade,
        não só na primeira carga da imagem). NÃO toca `self.mem` (RAM
        preserva conteúdo num reset que não é power-on, hardware real
        também preserva - só os SFRs voltam ao default)."""
        self.set_w16(0xFE10, 0xFC00)
        self.pc = 0
        self.flags = {'Z': False, 'N': False, 'C': False, 'V': False}
        self.set_special(SP_ADDR, DEFAULT_SP)
        # DPP0-3 são SFR comuns (0xFE00/02/04/06), sem tratamento especial -
        # só precisam do valor de reset padrão do C166 real: identidade
        # (janela lógica N mapeia pra página física N), confirmado pelo
        # próprio boot da Copa Clio (fixa DPP0/1/2 explicitamente e nunca
        # toca DPP3, contando com esse default continuar valendo).
        self.set_w16(0xFE00, 0)
        self.set_w16(0xFE02, 1)
        self.set_w16(0xFE04, 2)
        self.set_w16(0xFE06, 3)
        # marca como "já escrito pelo firmware" mesmo sendo nós que setamos -
        # senão uma leitura explícita de DPP3 (nunca reprogramado pelo boot
        # real) ativaria o mock por engano e corromperia a paginação
        # override ativo de EXTP/EXTS/EXTPR/EXTSR/EXTR/ATOMIC: None ou
        # (mode, value, count_restante). mode em
        # {'exts','extp','extsr','extpr','extr','atomic'}. Setado pelo
        # opcode e decrementado uma vez por instrução em step() (não em
        # _step_inner, pra não precisar tocar nos ~30 pontos de retorno
        # de dentro do dispatch).
        self.ext_active = None

    def _watchdog_reset(self):
        """WDT estourou (0x10000 sem `SRVWDT`) - reset interno de verdade
        (manual §13.1: "the watchdog timer will overflow and cause an
        internal reset"). Reseta registradores/SFRs de CPU pro default,
        preserva RAM (`self.mem` intocado - hardware real também preserva
        num reset que não é power-on), e reseta o estado dos mocks de
        periférico desta sessão (guardas de reentrância, deadlines
        sintéticos) já que SFRs reais também voltariam ao default."""
        self.wdt_reset_count += 1
        self._reset_cpu_registers()
        self._wdt = 0
        self._wdt_tick_accum = 0
        self._wdt_reload = 0
        self._hw_timer = 0
        self._hw_irq_sp_watermark = None
        self._pending_op_deadline = None
        self._asc0_rx_deadline = None

    def w16(self, addr):
        return self.mem[addr] | (self.mem[addr + 1] << 8)

    def sw16(self, addr):
        v = self.w16(addr)
        return v - 0x10000 if v >= 0x8000 else v

    def set_w16(self, addr, val):
        val &= 0xFFFF
        self.mem[addr] = val & 0xFF
        self.mem[addr + 1] = (val >> 8) & 0xFF

    def get_breg(self, nibble):
        """Lê registrador de byte RLn/RHn a partir do nibble empacotado como
        (regnum<<1)|sel (sel=0->L,1->H) - confirmado contra a imagem real da
        Copa Clio via Ghidra, ver nota em c166asm.py/BREG_RE."""
        n, sel = nibble >> 1, nibble & 1
        return (self.r[n] >> 8) & 0xFF if sel else self.r[n] & 0xFF

    def set_breg(self, nibble, val):
        n, sel = nibble >> 1, nibble & 1
        val &= 0xFF
        if sel:
            self.r[n] = (self.r[n] & 0x00FF) | (val << 8)
        else:
            self.r[n] = (self.r[n] & 0xFF00) | val

    def regfield_addr(self, b):
        if b >= 0xF0:
            return None, b & 0xF
        return 0xFE00 + 2 * b, None

    def _regfield_special(self, addr):
        return addr in (MDL_ADDR, MDH_ADDR, SP_ADDR)

    def _reg_sfr_base(self):
        """Base do campo 'reg' compacto pra SFR: 0xFE00 (padrão) ou 0xF000
        (ESFR) durante uma janela EXTR/EXTSR/EXTPR ativa - manual: essas 3
        (não EXTS/EXTP puros) redirecionam 'reg'/'bitoff'/'bitaddr' pro
        espaço de SFR estendido pelas N instruções seguintes."""
        if self.ext_active and self.ext_active[0] in ('extr', 'extsr', 'extpr'):
            return 0xF000
        return 0xFE00

    # --- mock genérico de periférico (timer/ADC/porta/status) ---------------
    # LISTA BRANCA EXPLÍCITA (não uma janela ampla): só os endereços que a
    # gente CONFIRMOU por análise ser hardware de verdade "correm livre"
    # (avançam 1/instrução a partir do último valor escrito). Qualquer outro
    # endereço em 0xF000-0xFFFF continua RAM normal - estável, exatamente o
    # que foi escrito por último.
    #
    # ACHADO 19/08/2026 (2 rodadas): a 1ª versão mockava a janela 0xF000-
    # 0xFFFF INTEIRA - quebrou de duas formas diferentes: (a) endereços NUNCA
    # escritos travavam mockando errado depois de escritos uma vez (timer
    # 0xFE52, corrigido trocando pra modelo "roda livre a partir da última
    # escrita"); (b) só isso ainda corrompia CONSTANTES/RAM comuns que
    # DEVERIAM ficar paradas - achado rastreando um march test de RAM real
    # ("CMP r12,[r0]" write-then-verify em 0xE000, seguido de "CMP
    # r12,0xFE1E" contra uma constante de calibração provavelmente fixa em
    # 0xFFFF, pra onde r12 converge matematicamente via SHL+OR) - mockar
    # 0xFF1E fazia ele nunca bater com o valor fixo esperado, loop infinito.
    # Conclusão: sem inspecionar caso a caso, mockar tudo é pior que não
    # mockar nada - só entram endereços confirmados individualmente.
    # CORRIGIDO 19/08/2026 (achado consultando manual_3286A/c167cr_userguide.pdf,
    # o "User's Manual" de periféricos do C167CR - tabela 22-4, SFR por endereço
    # com nome e valor de reset oficiais - diferente do c166ism.pdf/instruction
    # set manual usado no resto do projeto). A lista de "hardware confirmado"
    # de antes estava certa em achar que eram SFR reais, mas ERRADA em tratar
    # TODOS como "correm livre": 0xFF1E é uma CONSTANTE fixa (ONES, só
    # leitura, hardware trava em 0xFFFF pra sempre - meu mock incremental só
    # "funcionava" por sorte estatística, passando por 0xFFFF 1x a cada 65536
    # leituras), e 0xFF32/0xFE88 são registrador de CONFIG (PWMCON1) e de
    # CAPTURA (CC4) - ambos com reset real 0x0000, não timer nenhum; fazer
    # eles "correrem livre" contradiz o hardware documentado.
    # Periférico ASC0 (UART) de verdade - canal serial K-line de diagnóstico
    # (manual §11, endereços tabela 22-3; confirmado no firmware real via
    # `notas_desmontagem.md` §"Inicialização da serial de diagnóstico", que
    # mostra o boot programando exatamente esses 4 registradores pra
    # 10.400 baud, o padrão ISO 9141/KWP2000 da linha K). ACHADO 20/08/2026
    # (pedido do usuário pra conectar um scanner OBD2 real via TCP): o
    # firmware NÃO usa vetor de interrupção pra ASC0 - o superloop principal
    # faz POLLING de `S0RIC.S0RIR`/`S0TIC.S0TIR` (bit 7 de cada, manual §11.6)
    # e chama a ISR de recepção (`isr_asc0_receive`, file 0x3406) ele mesmo
    # (`notas_desmontagem.md`, "Polling de UART (não por IRQ aqui)") - então
    # simular certo aqui é só: (1) byte recebido -> grava `S0RBUF` + seta bit
    # `S0RIR`, o firmware descobre sozinho no próprio loop; (2) byte
    # transmitido -> firmware escreve `S0TBUF`, a gente captura na fila de
    # saída e seta `S0TIR` (transmissão "instantânea" - não modela baud rate
    # real nem timing de bit, só a troca de byte, mesma simplificação de
    # evento síncrono já aceita pro resto do simulador). Não modela erros
    # (framing/parity/overrun, `S0FE`/`S0PE`/`S0OE` em `S0CON`) nem o
    # double-buffering real de TX - suficiente pro protocolo request/response
    # do K-line, que não depende de nenhum dos dois.
    WDTCON_ADDR = 0xFFAE      # ver Sim._watchdog_reset() - byte alto = WDTREL
    # Calibração da taxa de tick do WDT (20/08/2026) - achado testando o
    # boot completo com watchdog ligado: o teste de march de RAM do POST
    # (`file 0x1B44-0x1BDE`) arma `WDTREL=0xB6` (orçamento de 18944 ticks)
    # e só re-serve `SRVWDT` de verdade 36504 ticks depois (medido rodando
    # o boot real com o reset temporariamente desligado) - quase o DOBRO do
    # orçamento, com a aproximação antiga "1 tick por instrução". Consistente
    # com o WDT real ticar a fCPU/2 enquanto essa região do código (bastante
    # `EXTP`/acesso indexado) tem CPI médio bem acima de 2 - 1 tick a cada 2
    # instruções (proporção 1:2) dá margem de sobra (37888 disponível pros
    # 36504 necessários, ~3.8% de folga) sem descaracterizar o achado
    # original do self-loop (`file 0x110320`): ainda reseta, só que em
    # 131072 passos em vez de 65536 - continua determinístico e finito.
    WDT_TICK_NUMERATOR = 1
    WDT_TICK_DENOMINATOR = 2
    UART_TBUF_ADDR = 0xFEB0   # S0TBUF - escrita = "byte enviado pela ECU"
    UART_RBUF_ADDR = 0xFEB2   # S0RBUF - leitura = "byte recebido" (read-only no hw real)
    UART_TIC_ADDR = 0xFF6C    # S0TIC  - bit 7 (0x80) = S0TIR, "TX completo"
    UART_RIC_ADDR = 0xFF6E    # S0RIC  - bit 7 (0x80) = S0RIR, "RX completo"
    UART_CON_ADDR = 0xFFB0    # S0CON  - bit 4 (0x10) = S0REN, "receptor habilitado"
    UART_BG_ADDR = 0xFEB4     # S0BG   - reload do baud rate (só armazenado, não usado)

    TIMER_ADDRS = {
        0xFE52,  # T1 (CAPCOM Timer 1) - confirmado na tabela 22-4, reset
                 # 0x0000, timer de contagem livre de verdade. Também achado
                 # empiricamente: par captura/decorrido no boot ("MOV
                 # r12,0xFE52"..."MOV r4,0xFE52;SUB r4,r12")
        0xFE44,  # T4 (GPT1 Timer 4) - confirmado na tabela 22-4, reset
                 # 0x0000, timer de contagem livre de verdade.
    }

    # Constantes de hardware fixas (nunca mudam, escritas são ignoradas -
    # confirmado na tabela 22-4): ONES sempre 0xFFFF, ZEROS sempre 0x0000.
    # ZEROS (0xFF1C) nunca apareceu travando nada ainda, incluído por
    # simetria/proatividade já que é a mesma família de registrador do ONES.
    CONST_ADDRS = {0xFF1E: 0xFFFF, 0xFF1C: 0x0000}

    # Bloco de registradores do controlador CAN on-chip (manual §18, "Organization of
    # Registers and Message Objects", Figure 18-3): janela de 256 bytes 0xEF00-0xEFFF,
    # DECODIFICADA COMO PERIFÉRICO (X-Bus), não como flash. O simulador carregava essa
    # faixa com os bytes crus do .bin (que nesse endereço é flash apagada, 0xFF) - por
    # isso uma tarefa real disparada por TRAP #0x40 (despachante de mensagem CAN, ver
    # README.md) ficava lendo IR=0xFF pra sempre em vez de "sem interrupção pendente"
    # e nunca tomava o caminho de saída idle. Identificado 19/08/2026 cruzando o achado
    # do simulador com notas_desmontagem.md §0b2/linha 2545 (POST testa exatamente essa
    # janela com padrão 0x55, rotulado "teste dos registradores do CAN") e §3045/3563
    # (uso real pós-boot já mapeado: 0xEF00=CSR, escrita de bit MSGVAL em objeto de
    # mensagem). Mock pragmático (não é uma implementação de controlador CAN de
    # verdade - só valores de reset/idle plausíveis pra desbloquear a exploração):
    # - 0xEF00 CSR: reset documentado "XX01H" (INIT=1 após reset, resto 0) - fixo.
    # - 0xEF02 IR: reset "XXXXH" (não especificado), mas semanticamente é "código da
    #   interrupção pendente" (0=nenhuma) - sem barramento CAN real conectado no
    #   simulador, não pode haver interrupção pendente de verdade, então 0x0000 é o
    #   valor coerente (idle) e também o que destrava o loop observado.
    # - Resto do bloco (BTR/GMS/UGML/LGML/UMLM/LMLM e os 15 objetos de mensagem,
    #   EF04-EFFF): reset "UUUUH"/"UUUUH" = não especificado pelo manual: usa 0x0000
    #   (objeto inválido/MSGVAL=0, config zerada) - mesma convenção já usada pra SFR
    #   não documentado no resto do simulador.
    CONST_ADDRS[0xEF00] = 0x0001
    for _can_addr in range(0xEF02, 0xF000, 2):
        CONST_ADDRS[_can_addr] = 0x0000

    # Endereços fora da tabela 22-4 (nem padrão C167CR nem ESFR documentado -
    # ou reservado, ou extensão específica do ASIC Sirius32 sem doc
    # disponível): ficam como registrador comum, SEM mock - o reset default
    # real do C167CR pra registrador não documentado tende a ser 0x0000
    # mesmo (área não implementada normalmente lê 0 ou flutua, mas 0 é a
    # suposição mais segura), e isso é exatamente o que já temos por padrão
    # sem fazer nada. 0xFF32 (PWMCON1) e 0xFE88 (CC4) SAÍRAM daqui de
    # propósito (ver nota acima) - continuam em 0x0000, o reset real deles,
    # e qualquer "divisão por zero" que isso cause é o simulador sinalizando
    # corretamente uma dependência de inicialização/evento ainda não
    # resolvida (ver README.md), não um bug pra mascarar com mock.
    # 0xFEAA e 0xFFBA: RESOLVIDO 19/08/2026, ver README.md. Não é registrador
    # desconhecido - é SFR oficialmente NÃO IMPLEMENTADO no chip real.
    # c167cr_userguide.pdf cobre explicitamente o derivado C167SR-LM (o chip
    # real da Copa Clio, confirmado por marcação física + artigo acadêmico -
    # ver notas/RESUMO.md §2.2-2.3) - não é "parente próximo", é o manual
    # certo. A Tabela 22-3 lista "all SFRs which are implemented" (exaustiva)
    # e o manual documenta o comportamento de endereço não implementado:
    # "Unused (E)SFR addresses are reserved for future members of the C166
    # Family" / "Non-implemented (reserved) SFR bits ... will always supply a
    # read value of '0'". Ou seja: 0x0000 (memória comum, sem mock) JÁ É o
    # valor real e correto aqui - a divisão por zero que isso causa reproduz
    # fielmente o hardware real (que trataria via trap de Classe A, não
    # implementado ainda - ver README.md "Limitações").

    def _mock_read(self, addr, real_val, mask=0xFFFF):
        if addr in self.CONST_ADDRS:
            return self.CONST_ADDRS[addr] & mask
        # incrementa 1 a cada LEITURA (não por instrução/ciclo elapsed): um
        # timer de hardware real avança em passos de 1 tick de clock, então
        # passa por TODO valor possível. Achado 19/08/2026: a versão anterior
        # (incremento proporcional a instruções decorridas, ~8/iteração no
        # loop que travava) pula certos valores pra sempre quando o passo
        # não divide o alvo - 0xFFFF é ímpar, incrementos de 8 nunca acertam.
        if addr in self.TIMER_ADDRS:
            v = (self._sfr_mock.get(addr, 0) + 1) & mask
            self._sfr_mock[addr] = v
            return v
        return real_val

    def _mock_write(self, addr, val):
        # CONST_ADDRS: escrita é ignorada de propósito (são "read only" na
        # tabela 22-4 - hardware real também ignora/trava essas escritas)
        if addr in self.CONST_ADDRS:
            return
        # escrita "arma"/reseta o contador pro valor escrito - dali em
        # diante volta a incrementar 1 por leitura a partir desse baseline
        if addr in self.TIMER_ADDRS:
            self._sfr_mock[addr] = val
        if addr == self.UART_TBUF_ADDR:
            self.uart_tx_queue.append(val & 0xFF)
            self.set_w16(self.UART_TIC_ADDR, self.w16(self.UART_TIC_ADDR) | 0x80)
        if addr == self.WDTCON_ADDR:
            # WDTREL = byte alto de WDTCON (manual §13.1) - só guardamos o
            # valor pra usar quando SRVWDT recarregar; não modelamos WDTIN
            # nem os flags de indicação de origem do reset (WDTR/SWR/SHWR/
            # LHWR) - achado no firmware real: nunca visto programando
            # WDTIN=1, sempre usa o default (fCPU/2).
            self._wdt_reload = (val >> 8) & 0xFF

    def read_regfield16(self, b):
        """Lê o campo 'reg' compacto de qualquer instrução word: 0xF0-0xFF é
        GPR (r0-r15); qualquer outro valor endereça um SFR direto em
        base+2*b (base = 0xFE00 ou 0xF000/ESFR durante EXTR/EXTSR/EXTPR) -
        usado o tempo todo em firmware real (ex.: 'MOV 0xFE10,#0xF606' no
        boot da Copa Clio), mas nosso próprio c166asm.py nunca emite essa
        forma (sempre usa nome de variável com endereço de 16 bits cheio),
        por isso não tinha sido implementado."""
        if b >= 0xF0:
            return self.r[b & 0xF]
        addr = self._reg_sfr_base() + 2 * b
        if self._regfield_special(addr):
            return self.get_special(addr)
        return self._mock_read(addr, self.w16(addr))

    def breg_field(self, b):
        """Campo 'reg' compacto de RwRb/RbRb puros (ex.: MOVBZ Rwn,Rbm) onde
        o byte inteiro já vem forçado como GPR (>=0xF0) por construção - não
        tem forma SFR alternativa nesses casos."""
        if b < 0xF0:
            raise Trap(f"forma de byte com SFR direto (reg=0x{b:02X}) não suportada pelo simulador")
        return b & 0xF

    def read_breg_field(self, b):
        """Campo 'reg' compacto das formas de BYTE (MOVB/ADDB/.../regd8,
        regmem, memreg): b>=0xF0 é GPR (RLn/RHn via get_breg); b<0xF0
        endereça o BYTE BAIXO do SFR em base+2*b (manual: "reg" de instrução
        de byte só alcança o byte baixo do SFR, nunca o alto) - achado ao
        travar no boot real com reg=0x8E (MOVB mem,reg)."""
        if b >= 0xF0:
            return self.get_breg(b & 0xF)
        addr = self._reg_sfr_base() + 2 * b
        return self._mock_read(addr, self.mem[addr], mask=0xFF)

    def write_breg_field(self, b, val):
        if b >= 0xF0:
            self.set_breg(b & 0xF, val)
            return
        addr = self._reg_sfr_base() + 2 * b
        self._mock_write(addr, val & 0xFF)
        self.mem[addr] = val & 0xFF

    def write_regfield16(self, b, val):
        if b >= 0xF0:
            self.r[b & 0xF] = val & 0xFFFF
            return
        addr = self._reg_sfr_base() + 2 * b
        if self._regfield_special(addr):
            self.set_special(addr, val)
        else:
            self._mock_write(addr, val)
            self.set_w16(addr, val)

    def translate_mem(self, logical):
        """Traduz um endereço lógico de 16 bits ('mem' de long/indirect
        addressing) pro endereço físico de 24 bits, seguindo o algoritmo real
        do C166 (manual Infineon §6.2/6.4): sem override ativo, os bits
        15-14 do endereço lógico escolhem DPP0-3 (SFR em 0xFE00/02/04/06) e
        a física = (DPPn & 0x3FF)<<14 | (lógico & 0x3FFF). Com EXTP/EXTPR
        ativo, a página vem do operando da instrução em vez do DPP
        selecionado pela janela. Com EXTS/EXTSR ativo, é um segmento cheio
        (física = seg<<16 | lógico, sem dividir em página de 16KB)."""
        if self.ext_active:
            mode, value, _ = self.ext_active
            if mode in ('extp', 'extpr'):
                return ((value & 0x3FF) << 14) | (logical & 0x3FFF)
            if mode in ('exts', 'extsr'):
                return ((value & 0xFF) << 16) | logical
        window = (logical >> 14) & 0x3
        dpp = self.w16(0xFE00 + 2 * window)
        return ((dpp & 0x3FF) << 14) | (logical & 0x3FFF)

    def mem_read16(self, logical):
        phys = self.translate_mem(logical)
        if self._regfield_special(phys):
            return self.get_special(phys)
        return self._mock_read(phys, self.w16(phys))

    def mem_write16(self, logical, val):
        phys = self.translate_mem(logical)
        if self._regfield_special(phys):
            self.set_special(phys, val)
        else:
            self._mock_write(phys, val)
            self.set_w16(phys, val)

    def mem_read8(self, logical):
        phys = self.translate_mem(logical)
        return self._mock_read(phys, self.mem[phys], mask=0xFF)

    def mem_write8(self, logical, val):
        phys = self.translate_mem(logical)
        self._mock_write(phys, val)
        self.mem[phys] = val & 0xFF

    def bitoff_word(self, qq):
        """Lê o word bit-addressable referenciado por um byte 'bitoff'
        (JB/JNB/JBC/JNBS/BSET/BCLR/BAND/BOR/BXOR/BCMP/BMOV/BMOVN) - RESOLVIDO
        19/08/2026 confirmando contra a imagem real via Ghidra (ver
        ferramentas_disassembly/c166dis.py bitoff_name()): 00-7F=RAM interna
        (0xFD00+2*qq), 80-EF=SFR (0xFF00+2*(qq&0x7F)), F0-FF=GPR direto.
        Sempre endereço FÍSICO fixo (página 0), não passa por DPP - mesma
        regra do campo 'reg' compacto."""
        if qq >= 0xF0:
            return self.r[qq & 0xF]
        addr = self._bitoff_addr(qq)
        if addr == self.PSW_ADDR:
            return self._psw_word()
        return self.w16(addr)

    # BUG achado e corrigido 02/09/2026: PSW (SFR 0xFF10) é mapeada em
    # self.mem, mas os bits N/C/V/Z NUNCA eram escritos ali - só existiam em
    # self.flags['Z'/'N'/'V'/'C'], atualizados por update_flags_add/sub. JMPR
    # cc_XX funciona (lê self.flags direto via cc_true()), mas JB/JNB/BSET/
    # BCLR/BAND/etc sobre 0xFF10.bit (idioma muito comum pra testar Carry após
    # ADD/SUB em rotinas de aritmética saturada, ex.: file 0x3B82C/0x3B850/
    # 0x3B954) liam bitoff_word() -> w16(0xFF10) -> memória crua, sempre
    # estável em 0 (ou no que sobrou de escrita direta), então o desvio nunca
    # refletia a flag real. Confirmado: 2 pares de entrada pra `ADD r12,r13`
    # que davam Carry=True numa chamada e Carry=False na outra levavam ao
    # MESMO desvio em "JNB 0xFF10.1,alvo".
    #
    # Layout de bits confirmado empiricamente (não é só a doc genérica da
    # Infineon): a MESMA convenção já estava codificada e testada em
    # _enter_trap()/RETI (push/pop de PSW no TRAP e interrupção de hardware,
    # ambos passando na suíte de regressão) - bit0=N, bit1=C, bit2=V, bit3=Z.
    # Bate com notas_desmontagem.md linha 603 ("usa o carry do PSW
    # (0xFF10.1)") e com GLOSSARIO.md ("bit1=C"). Reaproveitamos essa mesma
    # fórmula aqui em vez de reinventar.
    #
    # Limitação DELIBERADA: só a LEITURA de 0xFF10 via bitoff_word() (JB/JNB/
    # BAND/BOR/BXOR/BCMP/BMOV/BMOVN) é sintetizada a partir de self.flags.
    # ESCRITA em PSW por essas instruções (BSET/BCLR/set_bitoff_word) e
    # leitura genérica via w16()/MOV Rn,0xFF10 continuam batendo na memória
    # crua (nenhuma instância disso encontrada testando PSW no firmware até
    # agora - se aparecer, tratar do mesmo jeito).
    PSW_ADDR = 0xFF10

    def _psw_word(self):
        return ((1 if self.flags['N'] else 0)
                | (2 if self.flags['C'] else 0)
                | (4 if self.flags['V'] else 0)
                | (8 if self.flags['Z'] else 0))

    def _bitoff_addr(self, qq):
        if qq < 0x80:
            return 0xFD00 + 2 * qq
        return 0xFF00 + 2 * (qq & 0x7F)

    def set_bitoff_word(self, qq, word):
        if qq >= 0xF0:
            self.r[qq & 0xF] = word & 0xFFFF
            return
        self.set_w16(self._bitoff_addr(qq), word & 0xFFFF)

    def get_bit(self, qq, bitpos):
        return (self.bitoff_word(qq) >> bitpos) & 1

    def set_bit(self, qq, bitpos, val):
        word = self.bitoff_word(qq)
        word = (word | (1 << bitpos)) if val else (word & ~(1 << bitpos))
        self.set_bitoff_word(qq, word)

    def update_flags_sub(self, a, b, res, width=16):
        mask, signbit = (0xFFFF, 0x8000) if width == 16 else (0xFF, 0x80)
        self.flags['Z'] = (res & mask) == 0
        self.flags['N'] = (res & signbit) != 0
        sa, sb = (a >= signbit), (b >= signbit)
        sr = (res & signbit) != 0
        self.flags['V'] = (sa != sb) and (sr != sa)
        self.flags['C'] = a < b

    def update_flags_add(self, a, b, res, width=16):
        if width == 8:
            self.flags['Z'] = (res & 0xFF) == 0
            self.flags['N'] = (res & 0x80) != 0
            sa, sb = (a >= 0x80), (b >= 0x80)
            sr = (res & 0x80) != 0
            self.flags['V'] = (sa == sb) and (sr != sa)
            self.flags['C'] = res > 0xFF
            return
        self.flags['Z'] = (res & 0xFFFF) == 0
        self.flags['N'] = (res & 0x8000) != 0
        sa, sb = (a >= 0x8000), (b >= 0x8000)
        sr = (res & 0x8000) != 0
        self.flags['V'] = (sa == sb) and (sr != sa)
        self.flags['C'] = res > 0xFFFF

    def cc_true(self, cc):
        """Tabela completa das 16 condições (manual Infineon, Table 5 "Condition
        Code Encoding") - antes só cobria 9 das 16, achado ao travar no boot
        real da Copa Clio em cc_UGT (0xE). E (end-of-table, usado por
        CMPD/CMPI) não é rastreada no simulador - sempre False, o que só
        afeta cc_NET (raro, específico de loop de tabela)."""
        Z, N, V, C = self.flags['Z'], self.flags['N'], self.flags['V'], self.flags['C']
        E = False
        if cc == 0x0: return True              # UC
        if cc == 0x1: return not (Z or E)      # NET
        if cc == 0x2: return Z                 # Z / EQ
        if cc == 0x3: return not Z             # NZ / NE
        if cc == 0x4: return V                 # V
        if cc == 0x5: return not V             # NV
        if cc == 0x6: return N                 # N
        if cc == 0x7: return not N             # NN
        if cc == 0x8: return C                 # C / ULT
        if cc == 0x9: return not C             # NC / UGE
        if cc == 0xA: return not (Z or (N != V))  # SGT
        if cc == 0xB: return Z or (N != V)     # SLE
        if cc == 0xC: return N != V            # SLT
        if cc == 0xD: return N == V            # SGE
        if cc == 0xE: return not (Z or C)      # UGT
        if cc == 0xF: return Z or C            # ULE
        raise Trap(f"condição JMPR não suportada no simulador: cc={cc:#x}")

    def step(self):
        """Executa uma instrução e decrementa a janela EXTP/EXTS/EXTR/ATOMIC
        ativa (se houver) - separado de _step_inner pra não precisar tocar
        nos ~30 pontos de retorno do dispatch só pra decrementar um contador.
        Comparação por identidade (`is`) detecta se a própria instrução que
        acabou de rodar substituiu a janela por uma nova (não decrementa
        nesse caso - a nova janela vale a partir da PRÓXIMA instrução)."""
        self._cycle += 1  # "tempo" do mock de periférico (timer/ADC/status) - 1 tick/instrução
        window_before = self.ext_active
        result = self._step_inner()
        # Watchdog: calibrado 20/08/2026 - ver comentário grande em
        # __init__/`_wdt_tick_accum`. Overflow = reset interno de verdade
        # (manual §13.1) - checado DEPOIS da instrução completar (hardware
        # real também deixa o ciclo de barramento em andamento terminar
        # antes do reset), e retorna cedo pra não rodar os checks de
        # interrupção sintética com estado de CPU recém-resetado.
        if self._wdt_enabled:
            self._wdt_tick_accum += self.WDT_TICK_NUMERATOR
            while self._wdt_tick_accum >= self.WDT_TICK_DENOMINATOR:
                self._wdt_tick_accum -= self.WDT_TICK_DENOMINATOR
                self._wdt += 1
            if self._wdt >= 0x10000:
                self._watchdog_reset()
                return result
        if window_before is not None and self.ext_active is window_before:
            mode, value, remaining = window_before
            remaining -= 1
            self.ext_active = (mode, value, remaining) if remaining > 0 else None
        self._check_hw_timer_interrupt()
        self._check_pending_op_isr()
        self._check_asc0_rx_isr()
        return result

    def _step_inner(self):
        pc = self.pc
        op = self.mem[pc]

        if op == 0xCC:  # NOP - instrução real de verdade (2 bytes, não faz
                         # nada). CORRIGIDO 20/08/2026: antes qualquer NOP era
                         # tratado como "fim de programa" - heurística que só
                         # valia pros .asm de teste antigos, mas quebrava
                         # firmware real (NOP também é usado como padding de
                         # timing legítimo em código real, ex. file 0x19F8 da
                         # Copa Clio, entre dois MOV de configuração - parar
                         # ali cortava a exploração em 2 instruções). Fim de
                         # programa agora é detectado em run() via loop
                         # infinito auto-referente (JMPR $,$ etc.), convenção
                         # já usada pelo firmware real pros vetores não
                         # atribuídos ("loop-armadilha").
            self.pc += 2
            return True

        if op & 0x0F == 0x0D:  # JMPR cc,rel
            cc = (op >> 4) & 0xF
            rel = self.mem[pc + 1]
            if rel >= 0x80:
                rel -= 0x100
            target = pc + 2 + rel * 2
            self.pc = target if self.cc_true(cc) else pc + 2
            return True

        # --- blocos abaixo promovidos pro topo do dispatch em 20/08/2026
        # (otimização de desempenho, sem mudar NENHUMA lógica - só a ORDEM
        # dos `if op == .../if op in (...)`, que são todos mutuamente
        # exclusivos por construção, então reordenar é 100% equivalente em
        # comportamento). Medido com cProfile sobre um trecho real de boot
        # (Scenic 2.0 16v.bin, 400k instruções): esses eram os opcodes mais
        # frequentes que ainda estavam checados bem no meio/fim da cadeia
        # sequencial de `if` (`_step_inner` é uma função só, sem tabela de
        # despacho - o custo de achar o opcode certo é proporcional a QUANTOS
        # `if` vêm antes dele). Cópia original removida do lugar de baixo -
        # não há duplicação.
        if op == 0x46:  # CMP reg,#data16 - medido: ~10,6% de todas as instruções
            regb = self.mem[pc + 1]
            imm = self.w16(pc + 2)
            a = self.read_regfield16(regb)
            self.update_flags_sub(a, imm, a - imm)
            self.pc += 4
            return True

        if op in self.ALU_REGD16:  # ADD/XOR/AND/OR reg,#data16 - medido: 0x66
                                    # (AND) sozinho já é ~7% de todas as instruções
            regb = self.mem[pc + 1]
            imm = self.w16(pc + 2)
            a = self.read_regfield16(regb)
            self.write_regfield16(regb, self._alu_op(self.ALU_REGD16[op], a, imm))
            self.pc += 4
            return True

        if op == 0xA7:  # SRVWDT - CONSERTADO 20/08/2026 (antes no-op junto com
                        # EINIT/SRST): recarrega o WDT de verdade pro valor
                        # `WDTREL<<8` e zera o byte baixo (manual §13.1) -
                        # medido: ~4,3% de todas as instruções (escalonador
                        # alimenta o watchdog toda volta do loop)
            self._wdt = (self._wdt_reload << 8) & 0xFFFF
            self.pc += 4
            return True

        if op in (0xB5, 0xB7):  # EINIT/SRST: no-op no simulador (não implementado)
            self.pc += 4
            return True

        if op == 0x5C:  # SHL Rw,#data4 - medido: ~3,3% de todas as instruções
            b = self.mem[pc + 1]
            d, sh = b & 0xF, (b >> 4) & 0xF
            self.r[d] = self._shift(self.r[d], sh, 'SHL')
            self.pc += 2
            return True

        if op == 0x0A:  # BFLDL bitoffQ,#mask8,#data8 - medido: ~2% de todas as instruções
            qq = self.mem[pc + 1]
            data = self.mem[pc + 2]
            mask = self.mem[pc + 3]
            val = self.read_regfield16(qq)
            lo = (val & ~mask & 0xFF) | (data & mask)
            res = (val & 0xFF00) | lo
            self.write_regfield16(qq, res)
            self.flags['Z'] = res == 0
            self.flags['N'] = (res & 0x8000) != 0
            self.flags['V'] = False
            self.flags['C'] = False
            self.pc += 4
            return True
        # --- fim dos blocos promovidos

        if op == 0xF0:  # MOV Rw,Rw
            b = self.mem[pc + 1]
            val = self.r[b & 0xF]
            self.r[(b >> 4) & 0xF] = val
            self._mov_flags16(val)
            self.pc += 2
            return True

        if op == 0xE0:  # MOV Rw,#data4 ("E0 #n": nibble alto = imediato, baixo = reg)
            b = self.mem[pc + 1]
            val = (b >> 4) & 0xF
            self.r[b & 0xF] = val
            self._mov_flags16(val)
            self.pc += 2
            return True

        if op == 0xC6:  # SCXT reg,#data16: empilha valor atual de reg, depois carrega
                        # reg com o imediato (manual §5, "switch context")
            regb = self.mem[pc + 1]
            imm = self.w16(pc + 2)
            old = self.read_regfield16(regb)
            sp = (self.get_special(SP_ADDR) - 2) & 0xFFFF
            self.set_special(SP_ADDR, sp)
            self.set_w16(sp, old)
            self.write_regfield16(regb, imm)
            self.pc += 4
            return True

        if op == 0xE6:  # MOV reg,#data16 (reg pode ser GPR OU SFR direto - ver read/write_regfield16)
            regb = self.mem[pc + 1]
            imm = self.w16(pc + 2)
            self.write_regfield16(regb, imm)
            self._mov_flags16(imm)
            self.pc += 4
            return True

        if op == 0xF2:  # MOV reg, mem (mem = endereço LÓGICO, traduzido via DPP/override)
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            val = self.mem_read16(mem)
            self.write_regfield16(regb, val)
            self._mov_flags16(val)
            self.pc += 4
            return True

        if op == 0xF6:  # MOV mem, reg
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            val = self.read_regfield16(regb)
            self.mem_write16(mem, val)
            self._mov_flags16(val)
            self.pc += 4
            return True

        if op == 0x00:  # ADD Rw,Rw
            b = self.mem[pc + 1]
            d, s = (b >> 4) & 0xF, b & 0xF
            a, bb = self.r[d], self.r[s]
            res = a + bb
            self.update_flags_add(a, bb, res)
            self.r[d] = res & 0xFFFF
            self.pc += 2
            return True

        if op == 0x10:  # ADDC Rw,Rw (soma com carry de entrada)
            b = self.mem[pc + 1]
            d, s = (b >> 4) & 0xF, b & 0xF
            a, bb = self.r[d], self.r[s]
            cin = 1 if self.flags['C'] else 0
            res = a + bb + cin
            self.update_flags_add(a, bb + cin, res)
            self.r[d] = res & 0xFFFF
            self.pc += 2
            return True

        if op == 0x30:  # SUBC Rw,Rw (subtração com carry/borrow de entrada)
            b = self.mem[pc + 1]
            d, s = (b >> 4) & 0xF, b & 0xF
            a, bb = self.r[d], self.r[s]
            cin = 1 if self.flags['C'] else 0
            res = a - bb - cin
            self.update_flags_sub(a, bb + cin, res)
            self.r[d] = res & 0xFFFF
            self.pc += 2
            return True

        if op == 0x20:  # SUB Rw,Rw
            b = self.mem[pc + 1]
            d, s = (b >> 4) & 0xF, b & 0xF
            a, bb = self.r[d], self.r[s]
            res = a - bb
            self.update_flags_sub(a, bb, res)
            self.r[d] = res & 0xFFFF
            self.pc += 2
            return True

        if op == 0x26:  # SUB reg,#data16
            regb = self.mem[pc + 1]
            imm = self.w16(pc + 2)
            a = self.read_regfield16(regb)
            res = a - imm
            self.update_flags_sub(a, imm, res)
            self.write_regfield16(regb, res)
            self.pc += 4
            return True

        if op == 0x16:  # ADDC reg,#data16
            regb = self.mem[pc + 1]
            imm = self.w16(pc + 2)
            a = self.read_regfield16(regb)
            cin = 1 if self.flags['C'] else 0
            res = a + imm + cin
            self.update_flags_add(a, imm + cin, res)
            self.write_regfield16(regb, res)
            self.pc += 4
            return True

        if op == 0x36:  # SUBC reg,#data16
            regb = self.mem[pc + 1]
            imm = self.w16(pc + 2)
            a = self.read_regfield16(regb)
            cin = 1 if self.flags['C'] else 0
            res = a - imm - cin
            self.update_flags_sub(a, imm + cin, res)
            self.write_regfield16(regb, res)
            self.pc += 4
            return True

        if op == 0x40:  # CMP Rw,Rw
            b = self.mem[pc + 1]
            d, s = (b >> 4) & 0xF, b & 0xF
            self.update_flags_sub(self.r[d], self.r[s], self.r[d] - self.r[s])
            self.pc += 2
            return True

        if op == 0x42:  # CMP reg, mem
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            a = self.read_regfield16(regb)
            v = self.mem_read16(mem)
            self.update_flags_sub(a, v, a - v)
            self.pc += 4
            return True

        if op == 0x0B:  # MUL Rw,Rw (signed)
            b = self.mem[pc + 1]
            d, s = (b >> 4) & 0xF, b & 0xF
            a = self.r[d] - 0x10000 if self.r[d] >= 0x8000 else self.r[d]
            bb = self.r[s] - 0x10000 if self.r[s] >= 0x8000 else self.r[s]
            res = (a * bb) & 0xFFFFFFFF
            self.set_special(MDL_ADDR, res & 0xFFFF)
            self.set_special(MDH_ADDR, (res >> 16) & 0xFFFF)
            self.flags['Z'] = res == 0
            self.pc += 2
            return True

        if op == 0x1B:  # MULU Rw,Rw (sem sinal)
            b = self.mem[pc + 1]
            d, s = (b >> 4) & 0xF, b & 0xF
            res = (self.r[d] * self.r[s]) & 0xFFFFFFFF
            self.set_special(MDL_ADDR, res & 0xFFFF)
            self.set_special(MDH_ADDR, (res >> 16) & 0xFFFF)
            self.flags['Z'] = res == 0
            self.pc += 2
            return True

        if op == 0x4B:  # DIV reg (signed, MDL / reg)
            b = self.mem[pc + 1]
            divisor = self.read_regfield16(b)
            divisor_s = divisor - 0x10000 if divisor >= 0x8000 else divisor
            mdl = self.get_special(MDL_ADDR)
            mdl_s = mdl - 0x10000 if mdl >= 0x8000 else mdl
            if divisor_s == 0:
                self._div_by_zero()
            else:
                q = int(mdl_s / divisor_s)
                rem = mdl_s - q * divisor_s
                self.set_special(MDL_ADDR, q & 0xFFFF)
                self.set_special(MDH_ADDR, rem & 0xFFFF)
                self.flags['V'] = not (-0x8000 <= q <= 0x7FFF)
                self.flags['C'] = False
            self.pc += 2
            return True

        if op == 0x6B:  # DIVL reg (com sinal, MD de 32 bits / reg de 16 bits)
            b = self.mem[pc + 1]
            divisor = self.read_regfield16(b)
            divisor_s = divisor - 0x10000 if divisor >= 0x8000 else divisor
            md = (self.get_special(MDH_ADDR) << 16) | self.get_special(MDL_ADDR)
            md_s = md - 0x100000000 if md >= 0x80000000 else md
            if divisor_s == 0:
                self._div_by_zero()
            else:
                q = int(md_s / divisor_s)
                rem = md_s - q * divisor_s
                self.set_special(MDL_ADDR, q & 0xFFFF)
                self.set_special(MDH_ADDR, rem & 0xFFFF)
                self.flags['V'] = not (-0x8000 <= q <= 0x7FFF)
                self.flags['C'] = False
            self.pc += 2
            return True

        if op == 0x7B:  # DIVLU reg (sem sinal, MD de 32 bits / reg de 16 bits)
            b = self.mem[pc + 1]
            divisor = self.read_regfield16(b)
            md = (self.get_special(MDH_ADDR) << 16) | self.get_special(MDL_ADDR)
            if divisor == 0:
                self._div_by_zero()
            else:
                q = md // divisor
                self.set_special(MDL_ADDR, q & 0xFFFF)
                self.set_special(MDH_ADDR, (md % divisor) & 0xFFFF)
                self.flags['V'] = q > 0xFFFF
                self.flags['C'] = False
            self.pc += 2
            return True

        if op == 0x5B:  # DIVU reg (sem sinal, MDL / reg)
            b = self.mem[pc + 1]
            divisor = self.read_regfield16(b)
            mdl = self.get_special(MDL_ADDR)
            if divisor == 0:
                self._div_by_zero()
            else:
                self.set_special(MDL_ADDR, (mdl // divisor) & 0xFFFF)
                self.set_special(MDH_ADDR, (mdl % divisor) & 0xFFFF)
                self.flags['V'] = False
                self.flags['C'] = False
            self.pc += 2
            return True

        if op == 0x3C:  # ROR Rw,#data4 ("3C #n": nibble alto = imediato, baixo = reg -
                        # mesma convenção Rwd4 já usada em SHR/SHL/ASHR abaixo. Manual
                        # Table 3/detailed description "ROR Rwn,#data4" = "3C #n", 2
                        # bytes. Achado rodando ../mapas/Clio RS1 GrN.ori: parava aqui
                        # como opcode não suportado, único da família ROL/ROR usado até
                        # onde a exploração chegou.
            b = self.mem[pc + 1]
            d, sh = b & 0xF, (b >> 4) & 0xF
            self.r[d] = self._rotate(self.r[d], sh, 'ROR')
            self.pc += 2
            return True

        if op == 0x7C:  # SHR Rw,#data4 ("7C #n": nibble alto = imediato, baixo = reg)
            b = self.mem[pc + 1]
            d, sh = b & 0xF, (b >> 4) & 0xF
            self.r[d] = self._shift(self.r[d], sh, 'SHR')
            self.pc += 2
            return True

        if op == 0x2B:  # PRIOR Rwn,Rwm: normalização de ponto flutuante (manual §21.5,
                        # "indicating the position of the first set bit"/"aids in
                        # normalizing floating point numbers") - Rwn recebe a
                        # contagem de deslocamentos à esquerda necessária pra
                        # normalizar Rwm (deixar o bit mais significativo em 1); se
                        # Rwm==0, Rwn=0 e Z=1 (Rwm não é modificado).
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            src = self.r[m]
            if src == 0:
                self.r[n] = 0
                self.flags['Z'] = True
            else:
                shift = 0
                while not (src & 0x8000):
                    src = (src << 1) & 0xFFFF
                    shift += 1
                self.r[n] = shift
                self.flags['Z'] = False
            self.pc += 2
            return True

        if op == 0x6C:  # SHR Rw,Rw
            b = self.mem[pc + 1]
            d, s = (b >> 4) & 0xF, b & 0xF
            self.r[d] = self._shift(self.r[d], self.r[s] & 0xF, 'SHR')
            self.pc += 2
            return True

        if op == 0xBC:  # ASHR Rw,#data4 (aritmético - preserva sinal; "BC #n")
            b = self.mem[pc + 1]
            d, sh = b & 0xF, (b >> 4) & 0xF
            self.r[d] = self._shift(self.r[d], sh, 'ASHR')
            self.pc += 2
            return True

        if op == 0xAC:  # ASHR Rw,Rw
            b = self.mem[pc + 1]
            d, s = (b >> 4) & 0xF, b & 0xF
            self.r[d] = self._shift(self.r[d], self.r[s] & 0xF, 'ASHR')
            self.pc += 2
            return True

        if op == 0x4C:  # SHL Rw,Rw
            b = self.mem[pc + 1]
            d, s = (b >> 4) & 0xF, b & 0xF
            self.r[d] = self._shift(self.r[d], self.r[s] & 0xF, 'SHL')
            self.pc += 2
            return True

        if op == 0x70:  # OR Rw,Rw
            b = self.mem[pc + 1]
            d, s = (b >> 4) & 0xF, b & 0xF
            self.r[d] = (self.r[d] | self.r[s]) & 0xFFFF
            self.flags['Z'] = self.r[d] == 0
            self.pc += 2
            return True

        if op == 0x81:  # NEG reg
            b = self.mem[pc + 1]
            self.write_regfield16(b, (-self.read_regfield16(b)) & 0xFFFF)
            self.pc += 2
            return True

        if op == 0x60:  # AND Rw,Rw
            b = self.mem[pc + 1]
            d, s = (b >> 4) & 0xF, b & 0xF
            self.r[d] = (self.r[d] & self.r[s]) & 0xFFFF
            self.flags['Z'] = self.r[d] == 0
            self.pc += 2
            return True

        if op == 0x50:  # XOR Rw,Rw
            b = self.mem[pc + 1]
            d, s = (b >> 4) & 0xF, b & 0xF
            self.r[d] = (self.r[d] ^ self.r[s]) & 0xFFFF
            self.flags['Z'] = self.r[d] == 0
            self.pc += 2
            return True

        if op in self.ALU_REGMEM:  # ADD/SUB/XOR/AND/OR reg,mem
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            a = self.read_regfield16(regb)
            b = self.mem_read16(mem)
            self.write_regfield16(regb, self._alu_op(self.ALU_REGMEM[op], a, b))
            self.pc += 4
            return True

        if op in self.ALU_MEMREG:  # ADD/SUB/XOR/AND/OR mem,reg
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            a = self.mem_read16(mem)
            b = self.read_regfield16(regb)
            self.mem_write16(mem, self._alu_op(self.ALU_MEMREG[op], a, b))
            self.pc += 4
            return True

        if op in (0x12, 0x14, 0x32, 0x34):  # ADDC/SUBC reg,mem / mem,reg
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            is_memreg = op in (0x14, 0x34)
            is_addc = op in (0x12, 0x14)
            a = self.mem_read16(mem) if is_memreg else self.read_regfield16(regb)
            b = self.read_regfield16(regb) if is_memreg else self.mem_read16(mem)
            cin = 1 if self.flags['C'] else 0
            if is_addc:
                res = a + b + cin
                self.update_flags_add(a, b + cin, res)
            else:
                res = a - b - cin
                self.update_flags_sub(a, b + cin, res)
            if is_memreg:
                self.mem_write16(mem, res)
            else:
                self.write_regfield16(regb, res)
            self.pc += 4
            return True

        if op == 0x91:  # CPL reg (complemento de 1)
            b = self.mem[pc + 1]
            val = (~self.read_regfield16(b)) & 0xFFFF
            self.write_regfield16(b, val)
            self.flags['Z'] = val == 0
            self.pc += 2
            return True

        if op == 0xF1:  # MOVB Rbn,Rbm ("F1 nm": destino=nibble alto, fonte=baixo)
            b = self.mem[pc + 1]
            val = self.get_breg(b & 0xF)
            self.set_breg((b >> 4) & 0xF, val)
            self._mov_flags8(val)
            self.pc += 2
            return True

        if op == 0xE1:  # MOVB Rbn,#data4 ("E1 #n": imediato=nibble alto, destino=baixo)
            b = self.mem[pc + 1]
            val = (b >> 4) & 0xF
            self.set_breg(b & 0xF, val)
            self._mov_flags8(val)
            self.pc += 2
            return True

        if op == 0xE7:  # MOVB reg,#data8 ("E7 RR ## xx": RR=campo reg de byte, ##=imediato, xx=padding)
            regb = self.mem[pc + 1]
            imm = self.mem[pc + 2]
            self.write_breg_field(regb, imm)
            self._mov_flags8(imm)
            self.pc += 4
            return True

        if op in self.ALU_BYTE_REGD8:  # ADD/ADDC/SUB/SUBC/CMP/XOR/AND/ORB reg,#data8
            regb = self.mem[pc + 1]
            imm = self.mem[pc + 2]
            a = self.read_breg_field(regb)
            name = self.ALU_BYTE_REGD8[op]
            if name == 'CMP':
                self.update_flags_sub(a, imm, a - imm, width=8)
            elif name == 'ADDC':
                cin = 1 if self.flags['C'] else 0
                res = a + imm + cin
                self.update_flags_add(a, imm + cin, res, width=8)
                self.write_breg_field(regb, res & 0xFF)
            elif name == 'SUBC':
                cin = 1 if self.flags['C'] else 0
                res = a - imm - cin
                self.update_flags_sub(a, imm + cin, res, width=8)
                self.write_breg_field(regb, res & 0xFF)
            elif name in ('AND', 'OR', 'XOR'):
                res = {'AND': a & imm, 'OR': a | imm, 'XOR': a ^ imm}[name]
                self.write_breg_field(regb, res)
                self.flags['Z'] = res == 0
            else:  # ADD/SUB
                res = a + imm if name == 'ADD' else a - imm
                (self.update_flags_add if name == 'ADD' else self.update_flags_sub)(a, imm, res, width=8)
                self.write_breg_field(regb, res & 0xFF)
            self.pc += 4
            return True

        if op == 0xF3:  # MOVB reg,mem (reg = registrador de byte, "F3 RR MM MM")
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            val = self.mem_read8(mem)
            self.write_breg_field(regb, val)
            self._mov_flags8(val)
            self.pc += 4
            return True

        if op == 0xF7:  # MOVB mem,reg
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            val = self.read_breg_field(regb)
            self.mem_write8(mem, val)
            self._mov_flags8(val)
            self.pc += 4
            return True

        if op in (0x01, 0x21, 0x61, 0x71, 0x41):  # ADDB/SUBB/ANDB/ORB/CMPB Rbn,Rbm
            b = self.mem[pc + 1]
            dnib, snib = (b >> 4) & 0xF, b & 0xF
            a, bb = self.get_breg(dnib), self.get_breg(snib)
            if op == 0x01:
                res = a + bb
                self.update_flags_add(a, bb, res, width=8)
                self.set_breg(dnib, res & 0xFF)
            elif op == 0x21:
                res = a - bb
                self.update_flags_sub(a, bb, res, width=8)
                self.set_breg(dnib, res & 0xFF)
            elif op == 0x61:
                res = a & bb
                self.set_breg(dnib, res)
                self.flags['Z'] = res == 0
            elif op == 0x71:
                res = a | bb
                self.set_breg(dnib, res)
                self.flags['Z'] = res == 0
            elif op == 0x41:
                self.update_flags_sub(a, bb, a - bb, width=8)
            self.pc += 2
            return True

        if op in (0x03, 0x23, 0x63, 0x73, 0x43):  # ADDB/SUBB/ANDB/ORB/CMPB reg,mem
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            a, bb = self.read_breg_field(regb), self.mem_read8(mem)
            if op == 0x03:
                res = a + bb
                self.update_flags_add(a, bb, res, width=8)
                self.write_breg_field(regb, res & 0xFF)
            elif op == 0x23:
                res = a - bb
                self.update_flags_sub(a, bb, res, width=8)
                self.write_breg_field(regb, res & 0xFF)
            elif op == 0x63:
                res = a & bb
                self.write_breg_field(regb, res)
                self.flags['Z'] = res == 0
            elif op == 0x73:
                res = a | bb
                self.write_breg_field(regb, res)
                self.flags['Z'] = res == 0
            elif op == 0x43:
                self.update_flags_sub(a, bb, a - bb, width=8)
            self.pc += 4
            return True

        if op in (0x05, 0x25, 0x65, 0x75):  # ADDB/SUBB/ANDB/ORB mem,reg
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            a, bb = self.mem_read8(mem), self.read_breg_field(regb)
            if op == 0x05:
                res = a + bb
                self.update_flags_add(a, bb, res, width=8)
            elif op == 0x25:
                res = a - bb
                self.update_flags_sub(a, bb, res, width=8)
            elif op == 0x65:
                res = a & bb
                self.flags['Z'] = res == 0
            elif op == 0x75:
                res = a | bb
                self.flags['Z'] = res == 0
            self.mem_write8(mem, res)
            self.pc += 4
            return True

        if op == 0xC0:  # MOVBZ Rwn,Rbm ("C0 mn": fonte=nibble alto, destino=baixo)
            b = self.mem[pc + 1]
            _, n = self.regfield_addr(0xF0 | (b & 0xF))
            src = self.get_breg((b >> 4) & 0xF)
            self.r[n] = src
            self.flags['Z'] = src == 0  # MOVBZ: N sempre 0 (zero-extend nunca seta bit15)
            self.flags['N'] = False
            self.pc += 2
            return True

        if op == 0xC2:  # MOVBZ reg,mem (destino = registrador de PALAVRA, zero-extend)
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            src = self.mem_read8(mem)
            self.write_regfield16(regb, src)
            self.flags['Z'] = src == 0
            self.flags['N'] = False
            self.pc += 4
            return True

        if op == 0xD0:  # MOVBS Rwn,Rbm ("D0 mn": fonte=nibble alto, destino=baixo -
                        # mesma ordem invertida do MOVBZ acima, só sign-extend em vez de zero)
            b = self.mem[pc + 1]
            _, n = self.regfield_addr(0xF0 | (b & 0xF))
            src = self.get_breg((b >> 4) & 0xF)
            val = (src - 0x100) & 0xFFFF if src >= 0x80 else src
            self.r[n] = val
            self.flags['Z'] = src == 0
            self.flags['N'] = (src & 0x80) != 0
            self.pc += 2
            return True

        if op == 0xD2:  # MOVBS reg,mem (destino = registrador de PALAVRA, sign-extend)
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            src = self.mem_read8(mem)
            val = (src - 0x100) & 0xFFFF if src >= 0x80 else src
            self.write_regfield16(regb, val)
            self.flags['Z'] = src == 0
            self.flags['N'] = (src & 0x80) != 0
            self.pc += 4
            return True

        if op == 0xC5:  # MOVBZ mem,reg (fonte = registrador de byte, grava word zero-extended)
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            src = self.read_breg_field(regb)
            self.mem_write16(mem, src)
            self.flags['Z'] = src == 0
            self.flags['N'] = False
            self.pc += 4
            return True

        if op == 0xD5:  # MOVBS mem,reg (fonte = registrador de byte, grava word sign-extended)
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            src = self.read_breg_field(regb)
            val = (src - 0x100) & 0xFFFF if src >= 0x80 else src
            self.mem_write16(mem, val)
            self.flags['Z'] = src == 0
            self.flags['N'] = (src & 0x80) != 0
            self.pc += 4
            return True

        if op == 0x1A:  # BFLDH bitoffQ,#mask8,#data8 ("1A QQ ## @@": máscara ANTES do dado)
            qq = self.mem[pc + 1]
            mask = self.mem[pc + 2]
            data = self.mem[pc + 3]
            val = self.read_regfield16(qq)
            hi = ((val >> 8) & ~mask & 0xFF) | (data & mask)
            res = (hi << 8) | (val & 0xFF)
            self.write_regfield16(qq, res)
            self.flags['Z'] = res == 0
            self.flags['N'] = (res & 0x8000) != 0
            self.flags['V'] = False
            self.flags['C'] = False
            self.pc += 4
            return True


        if op == 0xEC:  # PUSH reg: (SP)<-(SP)-2 ; ((SP))<-(op1) - pré-decrementa
            b = self.mem[pc + 1]
            sp = (self.get_special(SP_ADDR) - 2) & 0xFFFF
            self.set_special(SP_ADDR, sp)
            self.set_w16(sp, self.read_regfield16(b))
            self.pc += 2
            return True

        if op == 0xFC:  # POP reg: (op1)<-((SP)) ; (SP)<-(SP)+2 - pós-incrementa
            b = self.mem[pc + 1]
            sp = self.get_special(SP_ADDR)
            self.write_regfield16(b, self.w16(sp))
            self.set_special(SP_ADDR, (sp + 2) & 0xFFFF)
            self.pc += 2
            return True

        if op == 0xBB:  # CALLR rel: empilha IP de retorno, desvia (mesmo segmento -
                        # rel soma só nos 16 bits baixos, igual JMPR)
            rel = self.mem[pc + 1]
            if rel >= 0x80:
                rel -= 0x100
            seg = pc & 0xFF0000
            ret_ip = (pc + 2) & 0xFFFF
            sp = (self.get_special(SP_ADDR) - 2) & 0xFFFF
            self.set_special(SP_ADDR, sp)
            self.set_w16(sp, ret_ip)
            self.pc = seg | ((ret_ip + rel * 2) & 0xFFFF)
            return True

        if op == 0xCB:  # RET: (IP)<-((SP)) ; (SP)<-(SP)+2 - mesmo segmento, CSP intocado
            sp = self.get_special(SP_ADDR)
            self.pc = (pc & 0xFF0000) | self.w16(sp)
            self.set_special(SP_ADDR, (sp + 2) & 0xFFFF)
            return True

        if op == 0xEA:  # JMPA cc,caddr: absoluto, mesmo segmento (CSP intocado)
            b1 = self.mem[pc + 1]
            cc = (b1 >> 4) & 0xF
            caddr = self.w16(pc + 2)
            self.pc = (pc & 0xFF0000) | caddr if self.cc_true(cc) else pc + 4
            return True

        if op == 0xE2:  # PCALL reg,caddr ("E2 RR MM MM", 4 bytes - manual "Push
                        # Word and Call Subroutine Absolute"): empilha o valor de
                        # `reg` (GPR ou SFR direto, mesmo campo compacto de
                        # sempre) e o IP de retorno, depois desvia pro endereço
                        # absoluto `caddr` NO MESMO SEGMENTO (intra-segment, como
                        # CALLA/JMPA - "branches to the absolute memory location",
                        # sem mencionar troca de segmento como CALLS/JMPS fazem).
                        # Ordem de push do manual: primeiro (tmp=reg), depois IP -
                        # IP fica no topo da pilha (SP menor), pra bater com RETP
                        # (que despilha IP primeiro, reg depois). Achado rodando
                        # ../mapas/Clio RS1 GrN.ori: parava aqui como "fora de
                        # escopo".
            regb = self.mem[pc + 1]
            caddr = self.w16(pc + 2)
            val = self.read_regfield16(regb)
            sp = self.get_special(SP_ADDR)
            sp = (sp - 2) & 0xFFFF
            self.set_w16(sp, val)
            sp = (sp - 2) & 0xFFFF
            self.set_w16(sp, (pc + 4) & 0xFFFF)
            self.set_special(SP_ADDR, sp)
            self.flags['Z'] = val == 0
            self.flags['N'] = (val & 0x8000) != 0
            self.pc = (pc & 0xFF0000) | caddr
            return True

        if op == 0xCA:  # CALLA cc,caddr: chama absoluto, mesmo segmento (empilha só IP)
            b1 = self.mem[pc + 1]
            cc = (b1 >> 4) & 0xF
            caddr = self.w16(pc + 2)
            if self.cc_true(cc):
                seg = pc & 0xFF0000
                ret_ip = (pc + 4) & 0xFFFF
                sp = (self.get_special(SP_ADDR) - 2) & 0xFFFF
                self.set_special(SP_ADDR, sp)
                self.set_w16(sp, ret_ip)
                self.pc = seg | caddr
            else:
                self.pc = pc + 4
            return True

        if op == 0x9C:  # JMPI cc,[Rw]: indireto, mesmo segmento
            b1 = self.mem[pc + 1]
            cc, rw = (b1 >> 4) & 0xF, b1 & 0xF
            caddr = self.r[rw]
            self.pc = (pc & 0xFF0000) | caddr if self.cc_true(cc) else pc + 2
            return True

        if op == 0xAB:  # CALLI cc,[Rw]: chama indireto, mesmo segmento (empilha só IP)
            b1 = self.mem[pc + 1]
            cc, rw = (b1 >> 4) & 0xF, b1 & 0xF
            if self.cc_true(cc):
                seg = pc & 0xFF0000
                caddr = self.r[rw]
                ret_ip = (pc + 2) & 0xFFFF
                sp = (self.get_special(SP_ADDR) - 2) & 0xFFFF
                self.set_special(SP_ADDR, sp)
                self.set_w16(sp, ret_ip)
                self.pc = seg | caddr
            else:
                self.pc = pc + 2
            return True

        if op == 0xFA:  # JMPS seg,caddr: incondicional, TROCA de segmento
            seg = self.mem[pc + 1]
            caddr = self.w16(pc + 2)
            self.pc = (seg << 16) | caddr
            return True

        if op == 0xDA:  # CALLS seg,caddr: chama inter-segmento (empilha CSP, depois IP -
                         # ordem confirmada no manual Infineon; RETS desfaz na ordem inversa)
            seg = self.mem[pc + 1]
            caddr = self.w16(pc + 2)
            csp = (pc >> 16) & 0xFF
            ret_ip = (pc + 4) & 0xFFFF
            sp = (self.get_special(SP_ADDR) - 2) & 0xFFFF
            self.set_special(SP_ADDR, sp)
            self.set_w16(sp, csp)
            sp = (sp - 2) & 0xFFFF
            self.set_special(SP_ADDR, sp)
            self.set_w16(sp, ret_ip)
            self.pc = (seg << 16) | caddr
            return True

        if op == 0xDB:  # RETS: pop IP, pop CSP (ordem inversa do CALLS)
            sp = self.get_special(SP_ADDR)
            ip = self.w16(sp)
            sp = (sp + 2) & 0xFFFF
            csp = self.w16(sp) & 0xFF
            sp = (sp + 2) & 0xFFFF
            self.set_special(SP_ADDR, sp)
            self.pc = (csp << 16) | ip
            return True

        if op == 0x9B:  # TRAP #trap7: software trap - empilha PSW, CSP, IP (manual
                        # §5.8) e desvia pra vetor = trap_number*4 no segmento 0
            trapno = self.mem[pc + 1] & 0x7F
            self._enter_trap(trapno * 4, (pc + 2) & 0xFFFF, (pc >> 16) & 0xFF)
            return True

        if op == 0xFB:  # RETI: pop IP, CSP, PSW (ordem inversa de TRAP/interrupção)
            sp = self.get_special(SP_ADDR)
            ip = self.w16(sp)
            sp = (sp + 2) & 0xFFFF
            csp = self.w16(sp) & 0xFF
            sp = (sp + 2) & 0xFFFF
            psw = self.w16(sp)
            sp = (sp + 2) & 0xFFFF
            self.set_special(SP_ADDR, sp)
            self.flags['N'] = bool(psw & 1)
            self.flags['C'] = bool(psw & 2)
            self.flags['V'] = bool(psw & 4)
            self.flags['Z'] = bool(psw & 8)
            self.pc = (csp << 16) | ip
            if self._hw_irq_sp_watermark is not None and sp >= self._hw_irq_sp_watermark:
                self._hw_irq_sp_watermark = None
            return True

        if op == 0xDC:  # EXTS/EXTP/EXTSR/EXTPR Rw,#irang2 (forma registrador -
                        # fórmula já validada em ferramentas_disassembly/trace.py
                        # decode_dc(), 91 instâncias confirmadas contra firmware real)
            b1 = self.mem[pc + 1]
            reg = (b1 >> 4) & 0xF
            irang2 = ((b1 >> 2) & 0x3) + 1
            mode = ['exts', 'extp', 'extsr', 'extpr'][b1 & 0x3]
            value = self.r[reg]
            self.ext_active = (mode, value, irang2)
            self.pc += 2
            return True

        if op == 0xD7:  # EXTP #pag,#irang2 (forma imediata - só EXTP, não tem
                         # variante EXTS/EXTSR/EXTPR imediata na tabela de opcodes)
            b1 = self.mem[pc + 1]
            irang2 = ((b1 >> 2) & 0x3) + 1
            page = ((b1 & 0x3) << 8) | self.mem[pc + 2]
            self.ext_active = ('extp', page, irang2)
            self.pc += 4
            return True

        if op == 0xD1:  # ATOMIC #irang2 (mode 0) / EXTR #irang2 (mode 2) -
                         # mesmo opcode, só o campo de modo no byte2 distingue
                         # (manual: "D1 :00##-0"=ATOMIC, "D1 :10##-0"=EXTR)
            b1 = self.mem[pc + 1]
            irang2 = ((b1 >> 2) & 0x3) + 1
            mode_bits = b1 & 0x3
            if mode_bits == 0:
                # ATOMIC só trava interrupção/trap por N instruções - não
                # modelamos interrupção, então não tem efeito observável aqui,
                # mas ainda registra a janela (irrelevante pra tradução de
                # endereço, mas mantém o modelo consistente/documentado)
                self.ext_active = ('atomic', None, irang2)
            elif mode_bits == 2:
                self.ext_active = ('extr', None, irang2)
            else:
                raise Trap(f"EXTR_ATOMIC com bits de modo não documentados (0x{mode_bits:X}) em pc=0x{pc:04X}")
            self.pc += 2
            return True

        if op in (0x8A, 0x9A, 0xAA, 0xBA):  # JB/JNB/JBC/JNBS bitaddrQ.q,rel
                                            # ("8A QQ rr q0" etc - byte3=rel,
                                            # byte4 nibble alto=bit; ver
                                            # bitoff_word()/nota em c166dis.py)
            qq = self.mem[pc + 1]
            rel = self.mem[pc + 2]
            if rel >= 0x80:
                rel -= 0x100
            q = (self.mem[pc + 3] >> 4) & 0xF
            bit = self.get_bit(qq, q)
            target = pc + 4 + rel * 2
            if op == 0x8A:      # JB: pula se bit=1
                take = bit == 1
            elif op == 0x9A:    # JNB: pula se bit=0
                take = bit == 0
            elif op == 0xAA:    # JBC: pula se bit=1, e limpa o bit
                take = bit == 1
                if take:
                    self.set_bit(qq, q, 0)
            else:                # JNBS: pula se bit=0, e seta o bit
                take = bit == 0
                if take:
                    self.set_bit(qq, q, 1)
            self.pc = target if take else pc + 4
            return True

        if op in self.BCLR_OPS or op in self.BSET_OPS:  # BCLR/BSET bitoff.N
            qq = self.mem[pc + 1]
            bitn = self.BCLR_OPS.get(op, self.BSET_OPS.get(op))
            self.set_bit(qq, bitn, op in self.BSET_OPS)
            self.pc += 2
            return True

        if op in self.IND_WORD_OPS:  # ADD/ADDC/SUB/SUBC/CMP/XOR/AND/OR Rw,#data3/[Ri]/[Ri+]
            b = self.mem[pc + 1]
            nreg = (b >> 4) & 0xF
            lo = b & 0xF
            if lo & 0x8:
                ii = lo & 0x3
                src = self.mem_read16(self.r[ii])
                if lo & 0x4:
                    self.r[ii] = (self.r[ii] + 2) & 0xFFFF
            else:
                src = lo & 0x7
            a = self.r[nreg]
            name = self.IND_WORD_OPS[op]
            if name == 'CMP':
                self.update_flags_sub(a, src, a - src)
            elif name == 'ADDC':
                cin = 1 if self.flags['C'] else 0
                res = a + src + cin
                self.update_flags_add(a, src + cin, res)
                self.r[nreg] = res & 0xFFFF
            elif name == 'SUBC':
                cin = 1 if self.flags['C'] else 0
                res = a - src - cin
                self.update_flags_sub(a, src + cin, res)
                self.r[nreg] = res & 0xFFFF
            else:
                self.r[nreg] = self._alu_op(name, a, src)
            self.pc += 2
            return True

        if op in self.IND_BYTE_OPS:  # ADDB/ADDCB/SUBB/SUBCB/CMPB/XORB/ANDB/ORB
            b = self.mem[pc + 1]
            dnib = (b >> 4) & 0xF
            lo = b & 0xF
            if lo & 0x8:
                ii = lo & 0x3
                src = self.mem_read8(self.r[ii])
                if lo & 0x4:
                    self.r[ii] = (self.r[ii] + 1) & 0xFFFF
            else:
                src = lo & 0x7
            a = self.get_breg(dnib)
            name = self.IND_BYTE_OPS[op]
            if name == 'CMP':
                self.update_flags_sub(a, src, a - src, width=8)
            elif name == 'ADDC':
                cin = 1 if self.flags['C'] else 0
                res = a + src + cin
                self.update_flags_add(a, src + cin, res, width=8)
                self.set_breg(dnib, res & 0xFF)
            elif name == 'SUBC':
                cin = 1 if self.flags['C'] else 0
                res = a - src - cin
                self.update_flags_sub(a, src + cin, res, width=8)
                self.set_breg(dnib, res & 0xFF)
            elif name in ('AND', 'OR', 'XOR'):
                res = {'AND': a & src, 'OR': a | src, 'XOR': a ^ src}[name]
                self.set_breg(dnib, res)
                self.flags['Z'] = res == 0
            else:  # ADD/SUB
                res = a + src if name == 'ADD' else a - src
                (self.update_flags_add if name == 'ADD' else self.update_flags_sub)(a, src, res, width=8)
                self.set_breg(dnib, res & 0xFF)
            self.pc += 2
            return True

        # MOV indireto (registrador GPR completo 0-15 como ponteiro, sem a
        # restrição R0-R3 da forma curta "ind" acima). Convenção do manual
        # confirmada em todas as variantes: byte2 "nm" -> nibble ALTO = n,
        # BAIXO = m, sempre (independente de qual aparece primeiro na
        # sintaxe) - ex. "MOV [Rwm],Rwn  B8 nm": n(valor)=alto, m(ponteiro)=baixo.
        if op == 0xA8:  # MOV Rwn,[Rwm]
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            val = self.mem_read16(self.r[m])
            self.r[n] = val
            self._mov_flags16(val)
            self.pc += 2
            return True

        if op == 0x98:  # MOV Rwn,[Rwm+] (pós-incrementa Rwm)
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            val = self.mem_read16(self.r[m])
            self.r[n] = val
            self._mov_flags16(val)
            self.r[m] = (self.r[m] + 2) & 0xFFFF
            self.pc += 2
            return True

        if op == 0xB8:  # MOV [Rwm],Rwn
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            val = self.r[n]
            self.mem_write16(self.r[m], val)
            self._mov_flags16(val)
            self.pc += 2
            return True

        if op == 0x88:  # MOV [-Rwm],Rwn (pré-decrementa Rwm)
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            self.r[m] = (self.r[m] - 2) & 0xFFFF
            val = self.r[n]
            self.mem_write16(self.r[m], val)
            self._mov_flags16(val)
            self.pc += 2
            return True

        if op == 0xC8:  # MOV [Rwn],[Rwm]
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            val = self.mem_read16(self.r[m])
            self.mem_write16(self.r[n], val)
            self._mov_flags16(val)
            self.pc += 2
            return True

        if op == 0xD8:  # MOV [Rwn+],[Rwm] (pós-incrementa Rwn, o ponteiro destino)
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            val = self.mem_read16(self.r[m])
            self.mem_write16(self.r[n], val)
            self._mov_flags16(val)
            self.r[n] = (self.r[n] + 2) & 0xFFFF
            self.pc += 2
            return True

        if op == 0xE8:  # MOV [Rwn],[Rwm+] (pós-incrementa Rwm, o ponteiro fonte)
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            val = self.mem_read16(self.r[m])
            self.mem_write16(self.r[n], val)
            self._mov_flags16(val)
            self.r[m] = (self.r[m] + 2) & 0xFFFF
            self.pc += 2
            return True

        if op == 0xD4:  # MOV Rwn,[Rwm+#data16] ("D4 nm ## ##": ponteiro+deslocamento)
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            disp = self.w16(pc + 2)
            val = self.mem_read16((self.r[m] + disp) & 0xFFFF)
            self.r[n] = val
            self._mov_flags16(val)
            self.pc += 4
            return True

        if op == 0xC4:  # MOV [Rwm+#data16],Rwn ("C4 nm ## ##")
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            disp = self.w16(pc + 2)
            val = self.r[n]
            self.mem_write16((self.r[m] + disp) & 0xFFFF, val)
            self._mov_flags16(val)
            self.pc += 4
            return True

        if op == 0xF4:  # MOVB Rbn,[Rwm+#data16] ("F4 nm ## ##")
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            disp = self.w16(pc + 2)
            val = self.mem_read8((self.r[m] + disp) & 0xFFFF)
            self.set_breg(n, val)
            self._mov_flags8(val)
            self.pc += 4
            return True

        if op == 0xE4:  # MOVB [Rwm+#data16],Rbn ("E4 nm ## ##")
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            disp = self.w16(pc + 2)
            val = self.get_breg(n)
            self.mem_write8((self.r[m] + disp) & 0xFFFF, val)
            self._mov_flags8(val)
            self.pc += 4
            return True

        # MOVB indireto - versão byte da família A8/98/B8/88/C8/D8/E8 acima,
        # mesma convenção "nm" (alto=n, baixo=m); n é registrador de byte
        # (RLn/RHn via get_breg/set_breg) quando aplicável, m sempre ponteiro
        # de palavra inteira (0-15). Incremento/decremento de ponteiro é de 1
        # byte aqui, não 2 (diferença real em relação à versão word).
        if op == 0xA9:  # MOVB Rbn,[Rwm]
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            val = self.mem_read8(self.r[m])
            self.set_breg(n, val)
            self._mov_flags8(val)
            self.pc += 2
            return True

        if op == 0x99:  # MOVB Rbn,[Rwm+] (pós-incrementa Rwm em 1)
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            val = self.mem_read8(self.r[m])
            self.set_breg(n, val)
            self._mov_flags8(val)
            self.r[m] = (self.r[m] + 1) & 0xFFFF
            self.pc += 2
            return True

        if op == 0xB9:  # MOVB [Rwm],Rbn
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            val = self.get_breg(n)
            self.mem_write8(self.r[m], val)
            self._mov_flags8(val)
            self.pc += 2
            return True

        if op == 0x89:  # MOVB [-Rwm],Rbn (pré-decrementa Rwm em 1)
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            self.r[m] = (self.r[m] - 1) & 0xFFFF
            val = self.get_breg(n)
            self.mem_write8(self.r[m], val)
            self._mov_flags8(val)
            self.pc += 2
            return True

        if op == 0xC9:  # MOVB [Rwn],[Rwm]
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            val = self.mem_read8(self.r[m])
            self.mem_write8(self.r[n], val)
            self._mov_flags8(val)
            self.pc += 2
            return True

        if op == 0xD9:  # MOVB [Rwn+],[Rwm] (pós-incrementa Rwn, ponteiro destino)
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            val = self.mem_read8(self.r[m])
            self.mem_write8(self.r[n], val)
            self._mov_flags8(val)
            self.r[n] = (self.r[n] + 1) & 0xFFFF
            self.pc += 2
            return True

        if op == 0xE9:  # MOVB [Rwn],[Rwm+] (pós-incrementa Rwm, ponteiro fonte)
            b = self.mem[pc + 1]
            n, m = (b >> 4) & 0xF, b & 0xF
            val = self.mem_read8(self.r[m])
            self.mem_write8(self.r[n], val)
            self._mov_flags8(val)
            self.r[m] = (self.r[m] + 1) & 0xFFFF
            self.pc += 2
            return True

        if op in (0x2A, 0x3A, 0x4A, 0x5A, 0x6A, 0x7A):  # BCMP/BMOVN/BMOV/BOR/BAND/BXOR
            # "6A QQ ZZ qz" (BAND) etc: sintaxe é "op1(Z.z), op2(Q.q)" mas o
            # byte-stream traz QQ antes de ZZ; nibble de byte4 segue a MESMA
            # ordem do byte-stream (alto=q de Q/fonte, baixo=z de Z/destino)
            # - ver nota igual em c166dis.py. op1=destino=Z.z, op2=fonte=Q.q.
            qq, zz, qz = self.mem[pc + 1], self.mem[pc + 2], self.mem[pc + 3]
            q, z = (qz >> 4) & 0xF, qz & 0xF
            src = self.get_bit(qq, q)
            dst = self.get_bit(zz, z)
            if op == 0x4A:      # BMOV: dest <- src
                self.set_bit(zz, z, src)
                self.flags['Z'] = src == 0
                self.flags['V'] = False
                self.flags['C'] = False
                self.flags['N'] = src == 1
            elif op == 0x3A:    # BMOVN: dest <- NOT src
                self.set_bit(zz, z, 0 if src else 1)
                self.flags['Z'] = src == 0
                self.flags['V'] = False
                self.flags['C'] = False
                self.flags['N'] = src == 1
            else:                # BAND/BOR/BXOR/BCMP: flags = combinação lógica
                                 # dos 2 bits (Z=NOR,V=OR,C=AND,N=XOR); BAND/BOR/
                                 # BXOR também escrevem o resultado em dest
                self.flags['Z'] = not (dst or src)
                self.flags['V'] = bool(dst or src)
                self.flags['C'] = bool(dst and src)
                self.flags['N'] = bool(dst != src)
                if op == 0x6A:   # BAND
                    self.set_bit(zz, z, dst & src)
                elif op == 0x5A:  # BOR
                    self.set_bit(zz, z, dst | src)
                elif op == 0x7A:  # BXOR
                    self.set_bit(zz, z, dst ^ src)
                # 0x2A BCMP: só compara, não escreve
            self.pc += 4
            return True

        # CMPI1/CMPI2/CMPD1/CMPD2: compara Rw,op2 (flags de SUB normal) e
        # DEPOIS incrementa (CMPI) ou decrementa (CMPD) Rw por 1 ou 2 -
        # idioma clássico de loop FOR (conta e compara na mesma instrução).
        # "80 #n" etc: mesma convenção de imediato-no-nibble-alto já usada
        # em MOV/SHR Rw,#data4.
        if op in (0x80, 0x90, 0xA0, 0xB0):  # CMPI1/CMPI2/CMPD1/CMPD2 Rw,#data4
            b = self.mem[pc + 1]
            n, imm = b & 0xF, (b >> 4) & 0xF
            a = self.r[n]
            self.update_flags_sub(a, imm, a - imm)
            delta = {0x80: 1, 0x90: 2, 0xA0: -1, 0xB0: -2}[op]
            self.r[n] = (a + delta) & 0xFFFF
            self.pc += 2
            return True

        if op in (0x86, 0x96, 0xA6, 0xB6):  # CMPI1/CMPI2/CMPD1/CMPD2 Rw,#data16
            regb = self.mem[pc + 1]
            imm = self.w16(pc + 2)
            # 'reg' compacto: pode ser GPR (regb>=0xF0) OU SFR direto
            # (regb<0xF0) - achado rodando ../mapas/Clio RS1 GrN.ori, que
            # bate CMPD2 com um operando SFR e travava aqui (regfield_addr()
            # devolve n=None nesse caso; self.r[None] explodia). Consertado
            # usando os mesmos helpers read_regfield16/write_regfield16 já
            # usados pro resto do simulador nesse campo.
            a = self.read_regfield16(regb)
            self.update_flags_sub(a, imm, a - imm)
            delta = {0x86: 1, 0x96: 2, 0xA6: -1, 0xB6: -2}[op]
            self.write_regfield16(regb, (a + delta) & 0xFFFF)
            self.pc += 4
            return True

        if op in (0x82, 0x92, 0xA2, 0xB2):  # CMPI1/CMPI2/CMPD1/CMPD2 Rw,mem
            regb = self.mem[pc + 1]
            mem = self.w16(pc + 2)
            # mesmo achado/conserto do bloco 0x86/0x96/0xA6/0xB6 acima.
            a = self.read_regfield16(regb)
            b = self.mem_read16(mem)
            self.update_flags_sub(a, b, a - b)
            delta = {0x82: 1, 0x92: 2, 0xA2: -1, 0xB2: -2}[op]
            self.write_regfield16(regb, (a + delta) & 0xFFFF)
            self.pc += 4
            return True

        if op == 0x84:  # MOV [Rwn], mem ("84 0n MM MM": nibble baixo=n, alto sempre 0)
            b = self.mem[pc + 1]
            n = b & 0xF
            mem = self.w16(pc + 2)
            val = self.mem_read16(mem)
            self.mem_write16(self.r[n], val)
            self._mov_flags16(val)
            self.pc += 4
            return True

        if op == 0x94:  # MOV mem, [Rwn]
            b = self.mem[pc + 1]
            n = b & 0xF
            mem = self.w16(pc + 2)
            val = self.mem_read16(self.r[n])
            self.mem_write16(mem, val)
            self._mov_flags16(val)
            self.pc += 4
            return True

        if op == 0xA4:  # MOVB [Rwn], mem (versão byte do 0x84)
            b = self.mem[pc + 1]
            n = b & 0xF
            mem = self.w16(pc + 2)
            val = self.mem_read8(mem)
            self.mem_write8(self.r[n], val)
            self._mov_flags8(val)
            self.pc += 4
            return True

        if op == 0xB4:  # MOVB mem, [Rwn] (versão byte do 0x94)
            b = self.mem[pc + 1]
            n = b & 0xF
            mem = self.w16(pc + 2)
            val = self.mem_read8(self.r[n])
            self.mem_write8(mem, val)
            self._mov_flags8(val)
            self.pc += 4
            return True

        raise Trap(f"opcode não suportado pelo simulador em pc=0x{pc:04X}: 0x{op:02X}")

    special = None

    def get_special(self, addr):
        if self.special is None:
            self.special = {}
        return self.special.get(addr, 0)

    def set_special(self, addr, val):
        if self.special is None:
            self.special = {}
        self.special[addr] = val & 0xFFFF

    def run(self, max_steps=MAX_STEPS):
        """Retorna True se parou (loop infinito auto-referente, fim natural
        do programa), False se bateu o limite de passos ainda avançando (ex.:
        fall.asm, que é um loop de simulação contínua sem instrução de parada
        - nesse caso o limite de passos define quantas iterações do loop
        físico rodam).

        CORRIGIDO 20/08/2026: antes, "parar" era detectado só por hitar um
        NOP - quebrava firmware real, que usa NOP como padding de timing
        legítimo (não fim de programa). Agora detecta fim de programa pela
        mesma convenção que o próprio firmware real usa pros vetores de
        interrupção não atribuídos (ver notas_desmontagem.md, "loops-
        armadilha"): uma instrução que fica saltando pro PRÓPRIO endereço
        (`pc` não muda entre steps seguidos) por muitos steps consecutivos.

        ACHADO no mesmo dia: `pc` parado por só 1 step NÃO basta - um
        busy-wait condicional de espera de hardware de verdade (ex.: `JNB
        $,$` esperando o semáforo `0xFD9A.15` em `file 0x1805C`, já
        documentado acima em "Validação" item 5) também fica com `pc`
        parado por várias iterações até a interrupção CC26 liberar o bit -
        detectar isso como "fim de programa" na 1ª repetição cortava a
        exploração bem antes da hora (achado comparando com o resultado já
        validado de 5M+ instruções). `_HALT_THRESHOLD` steps consecutivos no
        mesmo `pc` (bem maior que qualquer período de espera de hardware
        conhecido no projeto, ex. os 250 ciclos do CC26) distingue as duas
        situações: busy-wait real resolve bem antes disso, loop permanente
        nunca resolve."""
        prev_pc = self.pc
        same_pc_run = 0
        for _ in range(max_steps):
            self.step()
            if self.pc == prev_pc:
                same_pc_run += 1
                if same_pc_run >= self._HALT_THRESHOLD:
                    return True
            else:
                same_pc_run = 0
            prev_pc = self.pc
        return False

    _HALT_THRESHOLD = 10_000

    def uart_inject_rx_byte(self, byte):
        """Entrega 1 byte "recebido pela linha K" pro firmware - grava
        S0RBUF e seta S0RIC.S0RIR (bit 7). O firmware descobre sozinho no
        próprio polling do superloop (não existe vetor de interrupção pra
        isso, ver comentário em UART_TBUF_ADDR acima) e chama
        isr_asc0_receive() por conta própria. Não modela S0REN (receptor
        habilitado) nem overrun - simplificação documentada, pensada pro
        protocolo request/response do K-line (a ECU só entrega o próximo
        byte pra receber depois de já ter processado o anterior)."""
        self.set_w16(self.UART_RBUF_ADDR, byte & 0xFF)
        self.set_w16(self.UART_RIC_ADDR, self.w16(self.UART_RIC_ADDR) | 0x80)

    def uart_pop_tx_bytes(self):
        """Devolve e limpa a fila de bytes que a ECU escreveu em S0TBUF
        desde a última chamada - ver comentário em UART_TBUF_ADDR acima."""
        out = self.uart_tx_queue
        self.uart_tx_queue = []
        return out


def parse_vars_from_asm_output(assembler_stdout):
    """Extrai 'vars: NOME=0xADDR, ...' e 'labels: ...' da saída do c166asm.py."""
    vars_ = {}
    for line in assembler_stdout.splitlines():
        line = line.strip()
        if line.startswith('vars'):
            for tok in line.split(':', 1)[1].split(','):
                tok = tok.strip()
                if not tok:
                    continue
                name, addr = tok.split('=')
                vars_[name.strip()] = int(addr.strip(), 16)
    return vars_


def resolve_addr(tok, varmap):
    """Aceita nome de variável (se houver --syms) ou endereço hex cru (0x2A)."""
    if tok in varmap:
        return varmap[tok]
    return int(tok, 16)


if __name__ == '__main__':
    import subprocess
    if len(sys.argv) < 2:
        print("uso: c166sim.py <arquivo.bin> [--steps=N] [--syms=arquivo.asm] "
              "[NOME_ou_0xADDR=valor ...]", file=sys.stderr)
        sys.exit(1)

    bin_path = sys.argv[1]
    rest = sys.argv[2:]

    steps = MAX_STEPS
    syms_asm = None
    filtered = []
    for a in rest:
        if a.startswith('--steps='):
            steps = int(a.split('=', 1)[1])
        elif a.startswith('--syms='):
            syms_asm = a.split('=', 1)[1]
        else:
            filtered.append(a)
    inputs = dict(a.split('=', 1) for a in filtered)

    varmap = {}      # só variáveis de dado - usado pro dump final
    labelmap = {}    # rótulos de código - só serve pra resolver endereço de input, não entra no dump
    if syms_asm:
        out = subprocess.run([sys.executable, 'c166asm.py', syms_asm, '/dev/null'],
                              capture_output=True, text=True, check=True, cwd='.')
        varmap = parse_vars_from_asm_output(out.stdout)
        for line in out.stdout.splitlines():
            if line.strip().startswith('labels'):
                for tok in line.split(':', 1)[1].split(','):
                    tok = tok.strip()
                    if tok:
                        name, addr = tok.split('=')
                        labelmap[name.strip()] = int(addr.strip(), 16)

    with open(bin_path, 'rb') as f:
        image = f.read()

    sim = Sim(image)
    for tok, val in inputs.items():
        sim.set_w16(resolve_addr(tok, {**labelmap, **varmap}), int(val) & 0xFFFF)

    halted = sim.run(max_steps=steps)

    reason = "loop infinito auto-referente (fim natural do programa)" if halted else f"limite de {steps} passos (loop continua)"
    print(f"# {bin_path}: parou em pc=0x{sim.pc:04X} - motivo: {reason}")
    if varmap:
        print("# variáveis:")
        for name, addr in sorted(varmap.items(), key=lambda kv: kv[1]):
            print(f"  {name:12s} 0x{addr:04X} = {sim.sw16(addr)}")
    print("# registradores:")
    print("  " + " ".join(f"r{i}={sim.r[i]}" for i in range(4)))
