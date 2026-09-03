; firmware_min/boot.asm - firmware mínimo escrito à mão (dialeto real de
; c166asm.py) para o experimento "compilar reimplementacao_c e falar com um
; ELM327 de verdade" (ver plano do experimento). Este NÃO é o firmware real
; da Copa Clio - é um programa próprio, inspirado na lógica reconstruída em
; ../../reimplementacao_c/kline/ e .../scheduler/, mas escrito diretamente
; em asm porque o compilador do projeto (c167cc) não gera chamadas de
; função/parâmetros compatíveis com este montador (sem endereçamento
; indexado [Rw+#offset] - ver compiler/docs/limitations.md), nem tabela de
; vetores/startup (fora do escopo do c167cc por design).
;
; Escopo desta fase (comprometido, ver plano): reconhecer só UM quadro K-line
; de tamanho FIXO (6 bytes: FMT TGT SRC SID PID checksum, correspondente ao
; pedido OBD2 "0100" = Mode 01 PID 00) e responder com o quadro fixo
; conhecido (Mode 01 PID 00 é ✅ confirmado em
; ../../reimplementacao_c/kline/obd_legislado.c: resposta real
; "41 00 BE 3E 80 10", conferida contra o .bin e contra uma captura real de
; um Sirius32 K4M). Qualquer outro quadro é descartado silenciosamente (sem
; NRC) - decodificar o campo de tamanho do FMT genericamente e responder
; outros SIDs fica para uma fase futura.
;
; Formato de quadro adotado (ver simulador/obd2_bridge.py, decodificado
; 20/08/2026 em file 0x3474/0x3504, tabela file 0x3BA estado 4/5):
;   [FMT][TGT][SRC] + payload + checksum(soma 8-bit de tudo antes)
; Pedido:  FMT=0x82 (0x80|len=2) TGT=0x7A(nosso endereço) SRC=0xF1(ferramenta)
;          payload = 01 00           checksum = soma & 0xFF = 0xEE
; Resposta:FMT=0x86 (0x80|len=6) TGT=0xF1(ferramenta)      SRC=0x7A(nosso)
;          payload = 41 00 BE 3E 80 10    checksum = soma & 0xFF = 0xBE
;
; UART ASC0 por POLLING (não por interrupção real) - decisão deliberada:
; `c166sim.py` só sabe disparar a ISR síntética de RX apontando pro endereço
; físico da ISR do firmware REAL (`ASC0_RX_ISR_TARGET`, ver
; `_check_asc0_rx_isr`), que não existe na nossa imagem. Nosso próprio
; superloop (à semelhança do `ramo_b_step()` de
; ../../reimplementacao_c/scheduler/scheduler_sirius32.c, que também já
; modela RX como polling) testa `S0RIC.S0RIR` (bit 7 de 0xFF6E) direto -
; mais simples e evita esse descompasso de endereço.
;
; Sem tabela de vetores real: o próprio `c166sim.py` começa com pc=0 e não
; exige vetor de reset separado (ver README do simulador) - o primeiro byte
; do .bin já É o ponto de entrada.

    MOV SP, #0x0200     ; SP não é usado nesta fase (sem PUSH/POP/CALLR),
                         ; inicializado só por precaução/higiene
    MOV RX_COUNT, #0

; ---------------------------------------------------------------------
; Superloop: espera 1 byte por vez em S0RBUF, monta os 6 bytes do quadro
; de pedido num buffer fixo (RX_B0..RX_B5), sem laço/ponteiro (montador
; não suporta endereçamento indireto [Rw] com sintaxe própria ainda).
; ---------------------------------------------------------------------
POLL:
    MOV R0, 0xFF6E       ; S0RIC
    MOV R1, #0x0080      ; máscara do bit S0RIR
    AND R0, R1
    JMPR Z, POLL         ; nenhum byte novo -> continua esperando

    MOV R2, 0xFEB2        ; R2 = byte recebido (S0RBUF)

    MOV R0, 0xFF6E        ; limpa S0RIR (hardware real limpa sozinho só
    MOV R1, #0xFF7F       ; quando a ISR lê S0RBUF - aqui replicamos isso
    AND R0, R1            ; manualmente já que não há ISR)
    MOV 0xFF6E, R0

    MOV R0, RX_COUNT
    MOV R1, #0
    CMP R0, R1
    JMPR Z, ST0
    MOV R1, #1
    CMP R0, R1
    JMPR Z, ST1
    MOV R1, #2
    CMP R0, R1
    JMPR Z, ST2
    MOV R1, #3
    CMP R0, R1
    JMPR Z, ST3
    MOV R1, #4
    CMP R0, R1
    JMPR Z, ST4
    MOV R1, #5
    CMP R0, R1
    JMPR Z, ST5
    JMPA UC, RST_RX       ; estado inesperado (não deveria acontecer) - reinicia

ST0:
    MOV RX_B0, R2
    MOV RX_COUNT, #1
    JMPA UC, POLL
ST1:
    MOV RX_B1, R2
    MOV RX_COUNT, #2
    JMPA UC, POLL
ST2:
    MOV RX_B2, R2
    MOV RX_COUNT, #3
    JMPA UC, POLL
ST3:
    MOV RX_B3, R2
    MOV RX_COUNT, #4
    JMPA UC, POLL
ST4:
    MOV RX_B4, R2
    MOV RX_COUNT, #5
    JMPA UC, POLL
ST5:
    MOV RX_B5, R2
    MOV RX_COUNT, #0
    JMPA UC, FRAME_DONE

RST_RX:
    MOV RX_COUNT, #0
    JMPA UC, POLL

; ---------------------------------------------------------------------
; Quadro completo (6 bytes) - valida checksum, endereço e SID/PID antes
; de responder. Qualquer falha de validação descarta o quadro em silêncio
; (comportamento honesto dado o escopo: não sabemos como o firmware real
; reagiria a um quadro malformado nesta variante nunca observada rodando -
; ver ../../reimplementacao_c/scheduler/scheduler_sirius32.c sobre RAMO
; B/K-line nunca ter sido visto executando em captura real).
; ---------------------------------------------------------------------
FRAME_DONE:
    MOV R0, RX_B0
    MOV R1, RX_B1
    ADD R0, R1
    MOV R1, RX_B2
    ADD R0, R1
    MOV R1, RX_B3
    ADD R0, R1
    MOV R1, RX_B4
    ADD R0, R1
    MOV R1, #255
    AND R0, R1
    MOV R1, RX_B5
    CMP R0, R1
    JMPA NZ, POLL         ; checksum não bate -> descarta

    MOV R0, RX_B1         ; TGT deve ser o nosso endereço (0x7A)
    MOV R1, #0x7A
    CMP R0, R1
    JMPA NZ, POLL

    MOV R0, RX_B3         ; SID
    MOV R1, #1
    CMP R0, R1
    JMPA NZ, POLL

    MOV R0, RX_B4         ; PID
    MOV R1, #0
    CMP R0, R1
    JMPA NZ, POLL

; Reconhecido: Mode 01 PID 00 - responde com o quadro fixo já framed.
; TX é modelado como instantâneo em c166sim.py (escrever em S0TBUF já
; enfileira o byte de saída e seta S0TIR sozinho - ver UART_TBUF_ADDR em
; c166sim.py), então não é preciso esperar entre bytes.
    MOV 0xFEB0, #0x86
    MOV 0xFEB0, #0xF1
    MOV 0xFEB0, #0x7A
    MOV 0xFEB0, #0x41
    MOV 0xFEB0, #0x00
    MOV 0xFEB0, #0xBE
    MOV 0xFEB0, #0x3E
    MOV 0xFEB0, #0x80
    MOV 0xFEB0, #0x10
    MOV 0xFEB0, #0xBE

    JMPA UC, POLL
