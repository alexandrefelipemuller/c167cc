; 04_sid_14.asm - ClearDiagnosticInfo. Sessão básica: precisa do handshake
; sub 0x20 (arma) seguido de sub 0x6F (confirma) antes de disparar
; DTC_CLEAR(SELECTIVE_2) - ver kline_dispatcher.c. Sessão estendida: sub
; 0x00 é o "modo de calibração especial" (func_01a948, corpo NUNCA
; decompilado - NRC honesto, não inventado); sub 0x03 é um gate de
; segurança separado pro ClearDTC (GATE14_SUB3) - simplificado aqui pra um
; arme direto por repetição (mesmo mecanismo do SID 0x27 básico), em vez de
; replicar o dança de 3 estados 0x68/0x69/0x6A também aqui (ambos os gates
; são descritos como mecanismos de repetição/watchdog sem crypto - a
; simplificação não inventa comportamento nem valor, só reaproveita o
; padrão mais simples já usado no 0x27 básico em vez de duplicar o de 3
; estados por uma segunda vez).

    RESERVE GATE14_SUB3, #2

D14_HANDLE:
    MOV R0, SESSION
    MOV R1, #0x20
    CMP R0, R1
    JMPA Z, D14_EXT

    ; sessão básica
    MOV R0, RX_BUF+8               ; subfunção
    MOV R1, #0x20
    CMP R0, R1
    JMPA Z, D14_ARM_HANDSHAKE
    MOV R1, #0x6F
    CMP R0, R1
    JMPA Z, D14_CONFIRM_HANDSHAKE
    MOV R0, #0x14
    MOV R1, #0x12
    JMPA UC, S_SEND_NRC

D14_ARM_HANDSHAKE:
    MOV HANDSHAKE20_STATE, #1
    MOV TX_BUF+0, #0x54
    MOV TX_BUF+2, #0x20
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET

D14_CONFIRM_HANDSHAKE:
    MOV R0, HANDSHAKE20_STATE
    MOV R1, #1
    CMP R0, R1
    JMPA NZ, D14_HANDSHAKE_NRC

    MOV HANDSHAKE20_STATE, #0
    MOV R0, #2                      ; SELECTIVE_2
    CALLA UC, DTC_CLEAR
    MOV TX_BUF+0, #0x54
    MOV TX_BUF+2, #0x6F
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET

D14_HANDSHAKE_NRC:
    MOV R0, #0x14
    MOV R1, #0x22                   ; conditionsNotCorrect (sem 0x20 antes)
    JMPA UC, S_SEND_NRC

; -----------------------------------------------------------------
D14_EXT:
    MOV R0, RX_BUF+8
    MOV R1, #0x00
    CMP R0, R1
    JMPA Z, D14_CALIBRATION_STUB
    MOV R1, #0x03
    CMP R0, R1
    JMPA Z, D14_SUB3_GATE
    MOV R0, #0x14
    MOV R1, #0x12
    JMPA UC, S_SEND_NRC

D14_CALIBRATION_STUB:
    ; func_01a948 (modo de calibração especial, idle/fan) - corpo nunca
    ; decompilado (ver notas) - NRC honesto, mesmo padrão de
    ; obd_stub_not_decompiled() em obd_legislado.c
    MOV R0, #0x14
    MOV R1, #0x11                   ; serviceNotSupported
    JMPA UC, S_SEND_NRC

D14_SUB3_GATE:
    MOV R0, RX_BUF+10                ; código do gate (simplificado, ver cabeçalho)
    MOV R1, #0x64
    CMP R0, R1
    JMPA NZ, D14_SUB3_NRC

    MOV GATE14_SUB3, #0xFF
    MOV TX_BUF+0, #0x54
    MOV TX_BUF+2, #0x03
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET

D14_SUB3_NRC:
    MOV R0, #0x14
    MOV R1, #0x12
    JMPA UC, S_SEND_NRC
