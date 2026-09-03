; 05_sid_23.asm - ReadMemoryByAddress. Só sub 0x6B (abre sessão estendida)
; e sub 0x6C (fecha, reseta estado de segurança) têm corpo real conhecido -
; leitura de memória de verdade nunca foi decompilada (ver
; kline_dispatcher.c) - qualquer outra subfunção é NRC honesto.

D23_HANDLE:
    MOV R0, RX_BUF+8
    MOV R1, #0x6B
    CMP R0, R1
    JMPA Z, D23_OPEN_EXT
    MOV R1, #0x6C
    CMP R0, R1
    JMPA Z, D23_CLOSE_EXT
    MOV R0, #0x23
    MOV R1, #0x11                   ; leitura de memória real nunca decompilada
    JMPA UC, S_SEND_NRC

D23_OPEN_EXT:
    MOV SESSION, #0x20
    MOV TX_BUF+0, #0x63
    MOV TX_BUF+2, #0x6B
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET

D23_CLOSE_EXT:
    ; reset completo de estado de segurança (func_01b05a, confirmado
    ; campo a campo nas notas) - volta pra básica e zera todos os gates
    MOV SESSION, #0x10
    MOV GATE27_BASIC, #0
    MOV GATE27_EXT0, #0
    MOV GATE27_EXT1, #0
    MOV GATE14_SUB3, #0
    MOV HANDSHAKE20_STATE, #0
    MOV TX_BUF+0, #0x63
    MOV TX_BUF+2, #0x6C
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET
