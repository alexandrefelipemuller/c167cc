; 01_frame_codec.asm - RX/TX genérico por comprimento, substitui o bloco de
; 6 bytes fixos do boot.asm original. Formato ([FMT][TGT][SRC]+payload+
; checksum) documentado em obd2_bridge.py/kline_frame.c - FMT baixo 6 bits =
; comprimento do payload (1-63 no formato real; aqui CLAMPADO pra 1-16, ver
; RESERVE abaixo) - cap prático de 16 bytes de payload (nenhum SID em escopo
; tem payload real documentado acima de ~8 bytes). Declarado 0 ou >16 vira
; clamp (1 ou 16) em vez de rejeitar - garante realinhamento determinístico
; do próximo quadro mesmo quando o FMT recebido é lixo (o dado em si ainda
; falha checksum/SID na camada de sessão e é descartado lá, não aqui).

    RESERVE RX_BUF, #32   ; 16 slots (1 byte útil por slot, guardado como word)
    RESERVE TX_BUF, #32

; ---------------------------------------------------------------------
; F_FRAME_RX - enche RX_BUF com 1 quadro completo (bloqueante, por
; polling). Ao retornar: flag Z setada = checksum bateu, NZ = quadro
; descartado (checksum não bate) - RX_LEN/RX_BUF já preenchidos os dois
; casos, quem chama decide o que fazer.
; ---------------------------------------------------------------------
F_FRAME_RX:
    MOV RX_COUNT, #0
F_RXLOOP:
    MOV R0, 0xFF6E
    MOV R1, #0x0080
    AND R0, R1
    JMPR Z, F_RXLOOP

    MOV R2, 0xFEB2

    MOV R0, 0xFF6E
    MOV R1, #0xFF7F
    AND R0, R1
    MOV 0xFF6E, R0

    MOV R3, RX_COUNT
    SHL R3, #1
    MOV R4, #RX_BUF
    ADD R4, R3
    MOV [R4], R2

    MOV R3, RX_COUNT
    MOV R5, #0
    CMP R3, R5
    JMPR NZ, F_RX_NOTFIRST

    MOV R3, R2
    MOV R5, #0x3F
    AND R3, R5
    MOV R5, #0
    CMP R3, R5
    JMPR NZ, F_RX_LENOK_LOW
    MOV R3, #1
F_RX_LENOK_LOW:
    MOV R5, #16
    CMP R3, R5
    JMPR ULE, F_RX_LENOK_HIGH
    MOV R3, #16
F_RX_LENOK_HIGH:
    MOV RX_LEN, R3
    MOV R5, #4
    ADD R3, R5
    MOV RX_TOTAL, R3

F_RX_NOTFIRST:
    MOV R3, RX_COUNT
    MOV R5, #1
    ADD R3, R5
    MOV RX_COUNT, R3

    MOV R5, RX_TOTAL
    CMP R3, R5
    JMPA NZ, F_RXLOOP

    ; quadro completo (RX_COUNT == RX_TOTAL) - valida checksum
    MOV R0, RX_TOTAL
    MOV R5, #1
    SUB R0, R5              ; R0 = quantidade de bytes a somar (tudo menos o checksum)
    CALLR F_CHECKSUM_RXBUF  ; -> R1 = soma & 0xFF

    MOV R3, RX_TOTAL
    MOV R5, #1
    SUB R3, R5
    SHL R3, #1
    MOV R4, #RX_BUF
    ADD R4, R3
    MOV R2, [R4]             ; R2 = byte de checksum recebido
    CMP R1, R2               ; Z=bate, NZ=não bate - flag sobrevive ao RET
    RET

; ---------------------------------------------------------------------
; F_CHECKSUM_RXBUF - soma os primeiros R0 bytes de RX_BUF, devolve em R1
; (mascarado em 8 bits). Usado tanto pela validação de RX quanto poderia
; ser reaproveitado por outras somas sobre o mesmo buffer.
; ---------------------------------------------------------------------
F_CHECKSUM_RXBUF:
    MOV R4, #RX_BUF
    MOV R1, #0
    MOV R5, #0
F_CKRX_LOOP:
    CMP R5, R0
    JMPR Z, F_CKRX_DONE
    MOV R2, [R4+]
    ADD R1, R2
    MOV R3, #1
    ADD R5, R3
    JMPR UC, F_CKRX_LOOP
F_CKRX_DONE:
    MOV R3, #255
    AND R1, R3
    RET

; ---------------------------------------------------------------------
; F_FRAME_TX - transmite TX_LEN bytes de TX_BUF, já enquadrados (FMT/TGT
; fixo=0xF1/SRC fixo=0x7A/checksum). Quem chama já deixou TX_BUF e TX_LEN
; prontos antes de CALLR aqui.
; ---------------------------------------------------------------------
F_FRAME_TX:
    MOV R3, TX_LEN
    MOV R5, #0x80
    OR R3, R5
    MOV 0xFEB0, R3
    MOV R1, R3

    MOV R2, #0xF1
    MOV 0xFEB0, R2
    ADD R1, R2

    MOV R2, #0x7A
    MOV 0xFEB0, R2
    ADD R1, R2

    MOV R4, #TX_BUF
    MOV R5, #0
F_TXLOOP:
    CMP R5, TX_LEN
    JMPR Z, F_TX_SEND_CHECKSUM
    MOV R2, [R4+]
    MOV 0xFEB0, R2
    ADD R1, R2
    MOV R3, #1
    ADD R5, R3
    JMPR UC, F_TXLOOP
F_TX_SEND_CHECKSUM:
    MOV R3, #255
    AND R1, R3
    MOV 0xFEB0, R1
    RET
