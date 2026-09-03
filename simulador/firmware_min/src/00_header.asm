; firmware_min/src/00_header.asm - init + convenção de labels por fragmento.
; Ver plano do experimento "fluxo K-line proprietário completo". Fragmentos
; concatenados por build.py na ordem numérica (00..99) num único .asm, porque
; c166asm.py resolve labels/vars num único passe sobre tudo que recebe -
; não há diretiva de "include" nem linker de verdade (ver
; compiler/docs/limitations.md sobre o c167cc, mesma limitação estrutural
; aqui do lado do montador).
;
; Convenção de prefixo de label por fragmento (evita colisão no namespace
; global do assembler):
;   F_*    01_frame_codec.asm  (RX/TX genérico por comprimento)
;   S_*    02_session.asm      (máquina de sessão)
;   D27_*  03_sid_27.asm       (SecurityAccess)
;   D14_*  04_sid_14.asm       (ClearDiagnosticInfo)
;   D23_*  05_sid_23.asm       (ReadMemoryByAddress)
;   D2A_*  06_sid_2a.asm       (ReadDataByLocalId)
;   L_*    07_kline_legacy.asm (dispatcher de 5 códigos)
;   DTC_*  08_dtc_table.asm    (tabela de 60 DTCs + clear/decay/mark)
;   SCH_*  09_scheduler.asm    (cadência T1/ADC tick/CRC16)
;   MAIN   99_footer.asm       (entry point real)
;
; Convenção de registrador entre sub-rotinas (CALLR/RET, sem pilha de
; parâmetros disponível - mesma limitação do c167cc, contornável à mão):
;   R0 = entrada (subfunção/índice/valor)      R1 = saída (status/resultado)
;   R6/R7 = scratch reservado pelo PRÓPRIO montador (expansão de MOV
;   mem,#imm / mem,mem - ver cabeçalho de c166asm.py) - nunca usar R6/R7
;   pra guardar estado entre chamadas de sub-rotina.

    ; SP bem acima de código+dados (que cresce conforme o firmware cresce) -
    ; a pilha cresce PRA BAIXO a partir daqui. Achado 21/08/2026: com
    ; SP=0x0400 (usado no Stage 1, quando o código ainda cabia todo abaixo
    ; disso), o firmware cresceu e passou a ter CALLA aninhado o bastante
    ; pra pilha sobrescrever o próprio código (corrupção silenciosa,
    ; travava só depois de vários pushes). Achado de novo 21/08/2026, mesmo
    ; bug, ao integrar o módulo DTC compilado (dtc_sirius32.c inteiro, ~4KB
    ; de código + tabela de 60 entradas): código+dados passaram de ~0x805
    ; pra ~0x164D, ultrapassando o antigo SP=0x1000 - a pilha, crescendo
    ; PRA BAIXO a partir de 0x1000, já nasceria DENTRO do código/dados.
    ; 0x2000 dá margem real acima do fim atual dos dados (0x164D) sem
    ; chegar perto do espaço de SFR (0xFC00+).
    MOV SP, #0x2000
    JMPA UC, MAIN   ; pula os fragmentos de sub-rotina (definidos entre este
                    ; header e o footer) - sem isso a execução cai "por
                    ; gravidade" dentro do meio de F_FRAME_RX (o próximo
                    ; código no arquivo concatenado) sem ter sido chamada de
                    ; verdade, e o RET dela pop de uma pilha vazia (achado
                    ; 21/08/2026 depurando o Stage 1)
