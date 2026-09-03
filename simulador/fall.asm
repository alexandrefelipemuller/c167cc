;----------------------------------------------------------
; C167CR - Simulação de Queda Livre / Lançamento
;
; Todas as variáveis usam ponto fixo Q8.8
;
; 256 = 1.0
;
; Entradas:
;   Y0       posição inicial
;   V0       velocidade inicial
;   G        gravidade
;   DT       passo de tempo
;   E        coeficiente de restituição
;
; Saídas:
;   Y        posição atual
;   V        velocidade atual
;
; Exemplo:
;   Y0 = 100 * 256       ; 100 metros
;   V0 = 0               ; repouso
;   G  = 9.8 * 256       ; gravidade
;   DT = 0.1 * 256       ; 100 ms
;   E  = 0.8 * 256       ; quique de 80%
;
; Sintaxe real C166/C167: MUL exige dois GPRs explícitos (não aceita
; operando de memória nem opera implicitamente sobre MDL pré-carregado),
; então cada variável de memória usada como fator é carregada em R7 antes
; do MUL. MDH:MDL é sempre sobrescrito pelo próprio MUL - não é preciso
; (nem faz efeito) zerar MDH antes.
;
; Conversão Q16.16 -> Q8.8 (>>8 no produto de 32 bits inteiro, preservando
; sinal): usar só "MOV Rx,MDL / SHR Rx,#8" (ou ASHR) joga fora o MDH e dá
; resultado errado sempre que o produto passa de 16 bits com sinal - o bit
; 15 de MDL isolado não é o sinal do valor de 32 bits completo. A forma
; correta combina os dois registradores: bits 8-15 de MDL viram bits 0-7
; do resultado (SHR lógico) e bits 0-7 de MDH viram bits 8-15 (SHL), depois
; OR junta os dois - isso é (MDH:MDL) >> 8 de verdade, com sinal certo.
;----------------------------------------------------------

        MOV     R1, Y0             ; R1 = posição inicial
        MOV     R2, V0             ; R2 = velocidade inicial

LOOP:

        ;--------------------------------------------------
        ; y = y + v * dt
        ;--------------------------------------------------

        MOV     R7, DT
        MUL     R2, R7              ; MDH:MDL = v * dt

        MOV     R3, MDL             ; Resultado Q16.16, bits baixos
        SHR     R3, #8              ; bits 8-15 de MDL -> bits 0-7 do resultado
        MOV     R5, MDH             ; bits altos do produto de 32 bits
        SHL     R5, #8              ; bits 0-7 de MDH -> bits 8-15 do resultado
        OR      R3, R5              ; combina -> Q8.8 correto (com sinal)

        ADD     R1, R3              ; y = y + v*dt


        ;--------------------------------------------------
        ; v = v - g * dt
        ;--------------------------------------------------

        MOV     R6, G
        MOV     R7, DT
        MUL     R6, R7              ; MDH:MDL = g * dt

        MOV     R3, MDL
        SHR     R3, #8
        MOV     R5, MDH
        SHL     R5, #8
        OR      R3, R5              ; Q8.8 correto (com sinal)

        SUB     R2, R3              ; v = v - g*dt


        ;--------------------------------------------------
        ; Verifica se bateu no chão
        ;--------------------------------------------------

        CMP     R1, #0
        JMPR    cc_SGE, SALVA

        ;--------------------------------------------------
        ; Colisão com o chão
        ;
        ; y = 0
        ; v = -v * E
        ;--------------------------------------------------

        MOV     R1, #0              ; posição = chão

        NEG     R2                  ; inverte velocidade

        MOV     R7, E
        MUL     R2, R7              ; v = v * restituição

        MOV     R2, MDL
        SHR     R2, #8
        MOV     R5, MDH
        SHL     R5, #8
        OR      R2, R5              ; Q8.8 correto (com sinal)


SALVA:

        MOV     Y, R1               ; Salva posição
        MOV     V, R2               ; Salva velocidade

        JMPR    cc_UC, LOOP
