; 09_scheduler.asm - cadência do RAMO B (ver scheduler_sirius32.c
; ramo_b_step()): acumula T1, a cada 250 unidades = 1 "tick de ADC"; a cada
; 4º tick de ADC (1000 unidades) roda o que no firmware real era o
; handshake tick do wakeup elétrico - FORA DE ESCOPO aqui (kline_handshake.c
; não portado, ver plano), então esse slot fica só como contagem estrutural
; sem ação (documentado, não inventado); a cada 8º tick (2000 unidades)
; serve o watchdog (SRVWDT) - único efeito realmente exercitável aqui,
; já que não há ADC de verdade simulado (`brownout_sense_raw < 0x8E` sem
; efeito modelado, mesmo no C original). Decay de DTC roda a cada tick de
; ADC (250 unidades) - cadência exata do call site real não foi pinada nas
; notas, escolha de posicionamento documentada aqui.
;
; SCH_STEP deve ser chamado 1x por iteração do MAIN loop (ver 99_footer.asm)
; com o delta de T1 desde a última chamada em R0.

    RESERVE T1_ACC, #2
    RESERVE ADC_TICK_COUNT, #2

SCH_STEP:
    MOV R1, T1_ACC
    ADD R1, R0                       ; acumula o delta (R0) recebido de quem chama
    MOV T1_ACC, R1
    MOV R2, #250
    CMP R1, R2
    JMPA ULE, SCH_DONE               ; ainda não completou 250 unidades

    MOV T1_ACC, #0                   ; consome o acumulador

    MOV R1, ADC_TICK_COUNT
    MOV R2, #1
    ADD R1, R2
    MOV ADC_TICK_COUNT, R1

    CALLA UC, DTC_DECAY_TICK

    MOV R3, ADC_TICK_COUNT           ; contador cicla 1..8 (reset abaixo)

SCH_CHECK_4:
    MOV R2, #4
    CMP R3, R2
    JMPA NZ, SCH_CHECK_8
    ; 4º tick de ADC - handshake tick (fora de escopo, ver cabeçalho) - no-op

SCH_CHECK_8:
    MOV R2, #8
    CMP R3, R2
    JMPA NZ, SCH_MAYBE_RESET
    SRVWDT

SCH_MAYBE_RESET:
    MOV R2, #8
    CMP R3, R2
    JMPA ULE, SCH_DONE
    MOV ADC_TICK_COUNT, #0            ; reinicia a contagem (0..8)

SCH_DONE:
    RET
