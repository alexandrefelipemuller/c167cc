; 06_sid_2a.asm - ReadDataByLocalId. Chamada real confirmada até
; kline_legacy código 0x1C (ver kline_dispatcher.c) - mas o mapeamento real
; de 28 grupos de dado periódico nunca foi resolvido, então o efeito é só
; "abrir", sem dado real (L_STUB devolve sem escrever nada - NRC honesto).

D2A_HANDLE:
    MOV R0, #0x1C
    CALLA UC, L_DISPATCH
    MOV R0, #0x2A
    MOV R1, #0x11                    ; mapeamento real de dado nunca resolvido
    JMPA UC, S_SEND_NRC
