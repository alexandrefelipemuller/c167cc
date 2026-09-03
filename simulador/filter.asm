;----------------------------------------------------------
; C167CR - Filtro simples de sensor
;
; INPUT:
;   SENSOR0
;   SENSOR1
;   SENSOR2
;
; OUTPUT:
;   RESULT
;
; RESULT = (S0 + S1 + S2) / 3
; depois limita entre MIN e MAX
;
; Sintaxe real C166/C167: MOV não aceita imediato direto pra memória
; (MOV MDH,#0), então o zero passa por R6 antes.
;----------------------------------------------------------

        MOV     R0, SENSOR0       ; Carrega sensor 0
        MOV     R1, SENSOR1       ; Carrega sensor 1
        MOV     R2, SENSOR2       ; Carrega sensor 2

        ADD     R0, R1             ; S0 + S1
        ADD     R0, R2             ; S0 + S1 + S2

        MOV     MDL, R0
        MOV     R6, #0
        MOV     MDH, R6
        MOV     R3, #3

        DIV     R3                 ; Divide soma por 3

        MOV     R0, MDL            ; R0 = média

        CMP     R0, MIN
        JMPR    cc_SGE, CHECK_MAX

        MOV     R0, MIN            ; Saturação inferior

CHECK_MAX:
        CMP     R0, MAX
        JMPR    cc_SLE, STORE

        MOV     R0, MAX            ; Saturação superior

STORE:
        MOV     RESULT, R0

        JMPR    cc_UC, END

END:
        JMPR    cc_UC, END        ; loop auto-referente = fim de programa
                                  ; (não NOP - ver c166sim.py Sim.run())

