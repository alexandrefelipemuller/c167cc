; 02_session.asm - máquina de sessão (fechada/básica/estendida) + SID 0x29
; (abre básica) + roteamento por SID pra dentro da sessão atual. Ver
; reimplementacao_c/kline/kline_dispatcher.c (kwp_sid_handler_basic /
; kwp_mode20_handler) - sessão fechada só aceita SID 0x29; básica aceita
; 0x27/0x14/0x23/0x2A; estendida os mesmos + sub-funções extras do 0x14/0x23.
;
; Convenção de posição no quadro (RX_BUF, slots de 2 bytes cada, ver
; 01_frame_codec.asm): slot0=FMT, slot1=TGT, slot2=SRC, slot3=SID (payload[0],
; offset RX_BUF+6), slot4=subfunção (payload[1], offset RX_BUF+8),
; slot5=parâmetro extra (payload[2], offset RX_BUF+10) - usado só pelo canal
; indexado do SID 0x27 estendido.

    RESERVE SESSION, #2          ; 0x00 fechada / 0x10 básica / 0x20 estendida (default 0)
    RESERVE HANDSHAKE20_STATE, #2

; ---------------------------------------------------------------------
; S_DISPATCH - olha SESSION e o SID do quadro já recebido em RX_BUF,
; chama o handler certo. Sempre monta uma resposta em TX_BUF/TX_LEN e
; transmite via F_FRAME_TX antes de voltar (positiva ou NRC).
; ---------------------------------------------------------------------
S_DISPATCH:
    ; OBD-II legislado (Mode 01, SID 0x01) é um caminho SEPARADO do
    ; dispatcher proprietário Renault no firmware real - SAE J1979 não
    ; exige sessão/segurança nenhuma (ver obd_legislado.c, kline_dispatcher.c
    ; menciona handoff pra `obd_legislado_dispatch` fora deste dispatcher).
    ; Checado ANTES do estado de sessão por isso - funciona independente de
    ; SESSION.
    MOV R0, RX_BUF+6
    MOV R1, #0x01
    CMP R0, R1
    JMPA Z, S_OBD_LEGISLADO

    MOV R0, SESSION
    MOV R1, #0
    CMP R0, R1
    JMPA Z, S_SESS_CLOSED
    MOV R1, #0x10
    CMP R0, R1
    JMPA Z, S_SESS_BASIC
    MOV R1, #0x20
    CMP R0, R1
    JMPA Z, S_SESS_EXT
    RET                          ; sessão desconhecida - não deveria acontecer

; -----------------------------------------------------------------
S_SESS_CLOSED:
    MOV R0, RX_BUF+6             ; SID
    MOV R1, #0x29
    CMP R0, R1
    JMPA NZ, S_NRC_UNKNOWN_SID

    MOV SESSION, #0x10
    MOV TX_BUF+0, #0x69           ; 0x29+0x40 (resposta positiva, sem eco -
    MOV TX_LEN, #1                ; payload de 1 byte não tem subfunção real)
    CALLA UC, F_FRAME_TX
    RET

; -----------------------------------------------------------------
S_SESS_BASIC:
    MOV R0, RX_BUF+6
    MOV R1, #0x27
    CMP R0, R1
    JMPA Z, D27_HANDLE
    MOV R1, #0x14
    CMP R0, R1
    JMPA Z, D14_HANDLE
    MOV R1, #0x23
    CMP R0, R1
    JMPA Z, D23_HANDLE
    MOV R1, #0x2A
    CMP R0, R1
    JMPA Z, D2A_HANDLE
    JMPA UC, S_NRC_UNKNOWN_SID

; -----------------------------------------------------------------
S_SESS_EXT:
    MOV R0, RX_BUF+6
    MOV R1, #0x27
    CMP R0, R1
    JMPA Z, D27_HANDLE
    MOV R1, #0x14
    CMP R0, R1
    JMPA Z, D14_HANDLE
    MOV R1, #0x23
    CMP R0, R1
    JMPA Z, D23_HANDLE
    MOV R1, #0x2A
    CMP R0, R1
    JMPA Z, D2A_HANDLE
    JMPA UC, S_NRC_UNKNOWN_SID

; -----------------------------------------------------------------
; S_OBD_LEGISLADO - Mode 01 PID 00 (bitmap de PIDs suportados), único PID
; com corpo real confirmado (✅ conferido contra o .bin E contra uma
; captura real de um Sirius32 K4M - ver obd_legislado.c). Qualquer outro
; PID é NRC honesto (índice->grupo dos ~28 handlers reais nunca rastreado).
; -----------------------------------------------------------------
S_OBD_LEGISLADO:
    MOV R0, RX_LEN
    MOV R1, #2
    CMP R0, R1
    JMPA NZ, S_OBD_NRC

    MOV R0, RX_BUF+8              ; PID
    MOV R1, #0
    CMP R0, R1
    JMPA NZ, S_OBD_NRC

    MOV TX_BUF+0, #0x41
    MOV TX_BUF+2, #0x00
    MOV TX_BUF+4, #0xBE
    MOV TX_BUF+6, #0x3E
    MOV TX_BUF+8, #0x80
    MOV TX_BUF+10, #0x10
    MOV TX_LEN, #6
    CALLA UC, F_FRAME_TX
    RET

S_OBD_NRC:
    MOV R0, #0x01
    MOV R1, #0x12                  ; subFunctionNotSupported (PID)
    JMPA UC, S_SEND_NRC

; ---------------------------------------------------------------------
; S_SEND_NRC - resposta negativa padrão [0x7F][SID][NRC]. R0=SID, R1=NRC
; já setados por quem chama.
; ---------------------------------------------------------------------
S_SEND_NRC:
    MOV TX_BUF+0, #0x7F
    MOV TX_BUF+2, R0
    MOV TX_BUF+4, R1
    MOV TX_LEN, #3
    CALLA UC, F_FRAME_TX
    RET

S_NRC_UNKNOWN_SID:
    ; real: 4 pesos diferentes de contador de erro por tipo - simplificado
    ; pra um só, mesma simplificação já aceita em kline_dispatcher.c
    MOV R0, RX_BUF+6
    MOV R1, #0x11                ; serviceNotSupported
    JMPA UC, S_SEND_NRC
