;----------------------------------------------------------
; C167CR - Cálculo de Fatorial
;
; Entrada:
;   NUMERO      word (16 bits)
;
; Saída:
;   RESULTADO   double word (32 bits)
;
; Exemplo:
;   NUMERO = 5
;   RESULTADO = 120
;
; Sintaxe real C166/C167 (MOV só aceita reg<->mem ou reg<-imm, nunca
; mem<-imm nem mem<->mem; MUL exige dois GPRs explícitos; não existe DEC -
; ver base_conhecimento_comum para o motivo dessas restrições):
;----------------------------------------------------------

        MOV     R1, NUMERO        ; Load number from memory
        MOV     R2, #1            ; Result = 1

LOOP:
        CMP     R1, #1            ; Is N <= 1?
        JMPR    cc_SLE, FIM       ; Yes, finish

        MUL     R2, R1            ; MDH:MDL = R2 * R1 (result = current * N)

        MOV     R2, MDL           ; Keep low 16 bits
        SUB     R1, #1            ; N-- (não existe DEC na C166 real)

        JMPR    cc_UC, LOOP

FIM:
        MOV     R6, MDL           ; MOV mem,mem não existe: passa por R6
        MOV     RESULTADO, R6     ; Store low word
        MOV     R6, MDH
        MOV     RESULTADO+2, R6   ; Store high word

HALT:
        JMPR    cc_UC, HALT       ; End (loop auto-referente - convenção real
                                  ; de "vetor não atribuído", não NOP: NOP é
                                  ; instrução real de padding, não sinal de
                                  ; parada - ver c166sim.py Sim.run())
