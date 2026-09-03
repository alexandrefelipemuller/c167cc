; 03_sid_27.asm - SecurityAccess. Achado confirmado (17/08/2026, ver
; reimplementacao_c/kline/kline_dispatcher.c): NÃO é seed/key - é um gate
; de repetição/watchdog puro. Sessão básica: só sub 0x64 arma/rearma um
; único canal global. Sessão estendida: canal indexado (0 ou 1, byte extra
; em RX_BUF+10), sub 0x68 arma, 0x69 avança o acumulador, 0x6A verifica se
; já desarmou (chegou no placeholder de threshold - valor real nunca
; extraído, ver notas). GATE27_* decaem sozinhos no tick do watchdog do
; scheduler (09_scheduler.asm) - sem repetição, o acesso expira.

    RESERVE GATE27_BASIC, #2
    RESERVE GATE27_EXT0, #2
    RESERVE GATE27_EXT1, #2

D27_HANDLE:
    MOV R0, SESSION
    MOV R1, #0x10
    CMP R0, R1
    JMPA Z, D27_BASIC

    ; sessão estendida - canal indexado
    MOV R0, RX_BUF+10             ; canal (0 ou 1)
    MOV R1, #0
    CMP R0, R1
    JMPA Z, D27_EXT_CH0
    MOV R1, #1
    CMP R0, R1
    JMPA Z, D27_EXT_CH1
    MOV R0, #0x27
    MOV R1, #0x31                 ; requestOutOfRange (canal inválido)
    JMPA UC, S_SEND_NRC

D27_BASIC:
    MOV R0, RX_BUF+8              ; subfunção
    MOV R1, #0x64
    CMP R0, R1
    JMPA NZ, D27_BASIC_NRC

    MOV GATE27_BASIC, #0xFF       ; arma/rearma (0xFF = "canal aberto")
    MOV TX_BUF+0, #0x67
    MOV TX_BUF+2, #0x64
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET
D27_BASIC_NRC:
    MOV R0, #0x27
    MOV R1, #0x12                 ; subFunctionNotSupported
    JMPA UC, S_SEND_NRC

D27_EXT_CH0:
    MOV R2, #GATE27_EXT0          ; endereço da variável do canal 0 (imm_addr)
    JMPA UC, D27_EXT_COMMON
D27_EXT_CH1:
    MOV R2, #GATE27_EXT1
    JMPA UC, D27_EXT_COMMON

; R2 = endereço do gate do canal escolhido (0 ou 1)
D27_EXT_COMMON:
    MOV R0, RX_BUF+8
    MOV R1, #0x68
    CMP R0, R1
    JMPA Z, D27_EXT_ARM
    MOV R1, #0x69
    CMP R0, R1
    JMPA Z, D27_EXT_ADVANCE
    MOV R1, #0x6A
    CMP R0, R1
    JMPA Z, D27_EXT_CHECK
    MOV R0, #0x27
    MOV R1, #0x12
    JMPA UC, S_SEND_NRC

D27_EXT_ARM:
    MOV R3, #1
    MOV [R2], R3                  ; estado "armado" (1) - ainda não desbloqueado
    MOV TX_BUF+0, #0x67
    MOV TX_BUF+2, #0x68
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET

D27_EXT_ADVANCE:
    MOV R3, [R2]
    MOV R4, #0
    CMP R3, R4
    JMPA Z, D27_EXT_NOTARMED       ; sub 0x69 sem 0x68 antes -> gate não armado

    MOV R4, #0x10                  ; passo/limiar (placeholder - valor real
    ADD R3, R4                     ; de calibração nunca extraído, ver notas)
    MOV R4, #0xFF
    CMP R3, R4
    JMPR ULE, D27_EXT_ADVANCE_STORE
    MOV R3, R4                      ; satura em 0xFF = desbloqueado
D27_EXT_ADVANCE_STORE:
    MOV [R2], R3
    MOV TX_BUF+0, #0x67
    MOV TX_BUF+2, #0x69
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET
D27_EXT_NOTARMED:
    MOV R0, #0x27
    MOV R1, #0x22                  ; conditionsNotCorrect
    JMPA UC, S_SEND_NRC

D27_EXT_CHECK:
    MOV R3, [R2]
    MOV R4, #0xFF
    CMP R3, R4
    JMPA NZ, D27_EXT_LOCKED
    MOV TX_BUF+0, #0x67
    MOV TX_BUF+2, #0x6A
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET
D27_EXT_LOCKED:
    MOV R0, #0x27
    MOV R1, #0x22
    JMPA UC, S_SEND_NRC
