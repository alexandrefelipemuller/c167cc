; 07_kline_legacy.asm - dispatcher linear de 5 códigos (file 0x199CA, ver
; kline_legacy.c) - estrutura 100% confirmada (5 CMPB, sem tabela), mas só
; o código 0x10 (relay pra ClearDiagnosticInformation) tem corpo real
; conhecido. 0x1B/0x1C/0x1D/0x1E são stubs honestos (nunca decompilados).
; R0 = código legado na entrada.

L_DISPATCH:
    MOV R1, #0x10
    CMP R0, R1
    JMPA Z, L_CODE_10
    MOV R1, #0x1B
    CMP R0, R1
    JMPA Z, L_STUB
    MOV R1, #0x1C
    CMP R0, R1
    JMPA Z, L_STUB
    MOV R1, #0x1D
    CMP R0, R1
    JMPA Z, L_STUB
    MOV R1, #0x1E
    CMP R0, R1
    JMPA Z, L_STUB
    RET                              ; código desconhecido - não deveria acontecer

L_CODE_10:
    ; relay real pra ClearDiagnosticInformation (SELECTIVE_2, mesmo efeito
    ; do SID 0x14/0x6F já confirmado) - ver kline_legacy.c
    MOV R0, #2
    CALLA UC, DTC_CLEAR
    RET

L_STUB:
    ; corpo nunca decompilado (func_0x119A82/func_0x11a0c8) - efeito honesto:
    ; nenhum, quem chamou decide como reportar (D2A_HANDLE manda NRC)
    RET
