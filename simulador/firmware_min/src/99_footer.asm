; 99_footer.asm - entry point real: inicializa a tabela de DTC, entra no
; superloop de verdade (RX -> dispatch por sessão/SID -> resposta),
; chamando o scheduler a cada iteração.
;
; Simplificação deliberada do "tick" do scheduler: o RAMO B real avança a
; cadência a partir de um timer de hardware livre-corrente, independente de
; tráfego K-line. Aqui o superloop só acorda quando um quadro chega (bloqueia
; em F_FRAME_RX) - não há "tempo ocioso" de verdade pra amostrar um timer.
; Em vez disso, SCH_STEP é chamado 1x por transação completa com um delta
; sintético fixo (não é uma leitura real de T1) - suficiente pra exercitar a
; cadência/contadores, mas não é uma medida de tempo real decorrido.

MAIN:
    CALLA UC, DTC_INIT

MAIN_LOOP:
    CALLA UC, F_FRAME_RX
    JMPA NZ, MAIN_LOOP            ; checksum não bate -> descarta, volta a ouvir

    CALLA UC, S_DISPATCH

    MOV R0, #50                    ; delta sintético (ver comentário acima)
    CALLA UC, SCH_STEP

    JMPA UC, MAIN_LOOP
