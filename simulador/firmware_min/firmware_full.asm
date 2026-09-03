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

; 03_sid_27.asm - SecurityAccess. Achado confirmado (17/08/2026, ver
; reimplementacao_c/kline/kline_dispatcher.c): NÃO é seed/key - é um gate
; de repetição/watchdog puro. Sessão básica: só sub 0x64 arma/rearma um
; único canal global. Sessão estendida: canal indexado (0 ou 1, byte extra
; em RX_BUF+10), sub 0x68 arma, 0x69 avança o acumulador, 0x6A verifica se
; já desarmou (chegou no placeholder de threshold - valor real nunca
; extraído, ver notas). GATE27_* decaem sozinhos no tick do watchdog do
; scheduler (09_scheduler.asm) - sem repetição, o acesso expira.

    RESERVE GATE27_BASIC, #2
    RESERVE GATE27_EXT0, #2
    RESERVE GATE27_EXT1, #2

D27_HANDLE:
    MOV R0, SESSION
    MOV R1, #0x10
    CMP R0, R1
    JMPA Z, D27_BASIC

    ; sessão estendida - canal indexado
    MOV R0, RX_BUF+10             ; canal (0 ou 1)
    MOV R1, #0
    CMP R0, R1
    JMPA Z, D27_EXT_CH0
    MOV R1, #1
    CMP R0, R1
    JMPA Z, D27_EXT_CH1
    MOV R0, #0x27
    MOV R1, #0x31                 ; requestOutOfRange (canal inválido)
    JMPA UC, S_SEND_NRC

D27_BASIC:
    MOV R0, RX_BUF+8              ; subfunção
    MOV R1, #0x64
    CMP R0, R1
    JMPA NZ, D27_BASIC_NRC

    MOV GATE27_BASIC, #0xFF       ; arma/rearma (0xFF = "canal aberto")
    MOV TX_BUF+0, #0x67
    MOV TX_BUF+2, #0x64
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET
D27_BASIC_NRC:
    MOV R0, #0x27
    MOV R1, #0x12                 ; subFunctionNotSupported
    JMPA UC, S_SEND_NRC

D27_EXT_CH0:
    MOV R2, #GATE27_EXT0          ; endereço da variável do canal 0 (imm_addr)
    JMPA UC, D27_EXT_COMMON
D27_EXT_CH1:
    MOV R2, #GATE27_EXT1
    JMPA UC, D27_EXT_COMMON

; R2 = endereço do gate do canal escolhido (0 ou 1)
D27_EXT_COMMON:
    MOV R0, RX_BUF+8
    MOV R1, #0x68
    CMP R0, R1
    JMPA Z, D27_EXT_ARM
    MOV R1, #0x69
    CMP R0, R1
    JMPA Z, D27_EXT_ADVANCE
    MOV R1, #0x6A
    CMP R0, R1
    JMPA Z, D27_EXT_CHECK
    MOV R0, #0x27
    MOV R1, #0x12
    JMPA UC, S_SEND_NRC

D27_EXT_ARM:
    MOV R3, #1
    MOV [R2], R3                  ; estado "armado" (1) - ainda não desbloqueado
    MOV TX_BUF+0, #0x67
    MOV TX_BUF+2, #0x68
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET

D27_EXT_ADVANCE:
    MOV R3, [R2]
    MOV R4, #0
    CMP R3, R4
    JMPA Z, D27_EXT_NOTARMED       ; sub 0x69 sem 0x68 antes -> gate não armado

    MOV R4, #0x10                  ; passo/limiar (placeholder - valor real
    ADD R3, R4                     ; de calibração nunca extraído, ver notas)
    MOV R4, #0xFF
    CMP R3, R4
    JMPR ULE, D27_EXT_ADVANCE_STORE
    MOV R3, R4                      ; satura em 0xFF = desbloqueado
D27_EXT_ADVANCE_STORE:
    MOV [R2], R3
    MOV TX_BUF+0, #0x67
    MOV TX_BUF+2, #0x69
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET
D27_EXT_NOTARMED:
    MOV R0, #0x27
    MOV R1, #0x22                  ; conditionsNotCorrect
    JMPA UC, S_SEND_NRC

D27_EXT_CHECK:
    MOV R3, [R2]
    MOV R4, #0xFF
    CMP R3, R4
    JMPA NZ, D27_EXT_LOCKED
    MOV TX_BUF+0, #0x67
    MOV TX_BUF+2, #0x6A
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET
D27_EXT_LOCKED:
    MOV R0, #0x27
    MOV R1, #0x22
    JMPA UC, S_SEND_NRC

; 04_sid_14.asm - ClearDiagnosticInfo. Sessão básica: precisa do handshake
; sub 0x20 (arma) seguido de sub 0x6F (confirma) antes de disparar
; DTC_CLEAR(SELECTIVE_2) - ver kline_dispatcher.c. Sessão estendida: sub
; 0x00 é o "modo de calibração especial" (func_01a948, corpo NUNCA
; decompilado - NRC honesto, não inventado); sub 0x03 é um gate de
; segurança separado pro ClearDTC (GATE14_SUB3) - simplificado aqui pra um
; arme direto por repetição (mesmo mecanismo do SID 0x27 básico), em vez de
; replicar o dança de 3 estados 0x68/0x69/0x6A também aqui (ambos os gates
; são descritos como mecanismos de repetição/watchdog sem crypto - a
; simplificação não inventa comportamento nem valor, só reaproveita o
; padrão mais simples já usado no 0x27 básico em vez de duplicar o de 3
; estados por uma segunda vez).

    RESERVE GATE14_SUB3, #2

D14_HANDLE:
    MOV R0, SESSION
    MOV R1, #0x20
    CMP R0, R1
    JMPA Z, D14_EXT

    ; sessão básica
    MOV R0, RX_BUF+8               ; subfunção
    MOV R1, #0x20
    CMP R0, R1
    JMPA Z, D14_ARM_HANDSHAKE
    MOV R1, #0x6F
    CMP R0, R1
    JMPA Z, D14_CONFIRM_HANDSHAKE
    MOV R0, #0x14
    MOV R1, #0x12
    JMPA UC, S_SEND_NRC

D14_ARM_HANDSHAKE:
    MOV HANDSHAKE20_STATE, #1
    MOV TX_BUF+0, #0x54
    MOV TX_BUF+2, #0x20
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET

D14_CONFIRM_HANDSHAKE:
    MOV R0, HANDSHAKE20_STATE
    MOV R1, #1
    CMP R0, R1
    JMPA NZ, D14_HANDSHAKE_NRC

    MOV HANDSHAKE20_STATE, #0
    MOV R0, #2                      ; SELECTIVE_2
    CALLA UC, DTC_CLEAR
    MOV TX_BUF+0, #0x54
    MOV TX_BUF+2, #0x6F
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET

D14_HANDSHAKE_NRC:
    MOV R0, #0x14
    MOV R1, #0x22                   ; conditionsNotCorrect (sem 0x20 antes)
    JMPA UC, S_SEND_NRC

; -----------------------------------------------------------------
D14_EXT:
    MOV R0, RX_BUF+8
    MOV R1, #0x00
    CMP R0, R1
    JMPA Z, D14_CALIBRATION_STUB
    MOV R1, #0x03
    CMP R0, R1
    JMPA Z, D14_SUB3_GATE
    MOV R0, #0x14
    MOV R1, #0x12
    JMPA UC, S_SEND_NRC

D14_CALIBRATION_STUB:
    ; func_01a948 (modo de calibração especial, idle/fan) - corpo nunca
    ; decompilado (ver notas) - NRC honesto, mesmo padrão de
    ; obd_stub_not_decompiled() em obd_legislado.c
    MOV R0, #0x14
    MOV R1, #0x11                   ; serviceNotSupported
    JMPA UC, S_SEND_NRC

D14_SUB3_GATE:
    MOV R0, RX_BUF+10                ; código do gate (simplificado, ver cabeçalho)
    MOV R1, #0x64
    CMP R0, R1
    JMPA NZ, D14_SUB3_NRC

    MOV GATE14_SUB3, #0xFF
    MOV TX_BUF+0, #0x54
    MOV TX_BUF+2, #0x03
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET

D14_SUB3_NRC:
    MOV R0, #0x14
    MOV R1, #0x12
    JMPA UC, S_SEND_NRC

; 05_sid_23.asm - ReadMemoryByAddress. Só sub 0x6B (abre sessão estendida)
; e sub 0x6C (fecha, reseta estado de segurança) têm corpo real conhecido -
; leitura de memória de verdade nunca foi decompilada (ver
; kline_dispatcher.c) - qualquer outra subfunção é NRC honesto.

D23_HANDLE:
    MOV R0, RX_BUF+8
    MOV R1, #0x6B
    CMP R0, R1
    JMPA Z, D23_OPEN_EXT
    MOV R1, #0x6C
    CMP R0, R1
    JMPA Z, D23_CLOSE_EXT
    MOV R0, #0x23
    MOV R1, #0x11                   ; leitura de memória real nunca decompilada
    JMPA UC, S_SEND_NRC

D23_OPEN_EXT:
    MOV SESSION, #0x20
    MOV TX_BUF+0, #0x63
    MOV TX_BUF+2, #0x6B
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET

D23_CLOSE_EXT:
    ; reset completo de estado de segurança (func_01b05a, confirmado
    ; campo a campo nas notas) - volta pra básica e zera todos os gates
    MOV SESSION, #0x10
    MOV GATE27_BASIC, #0
    MOV GATE27_EXT0, #0
    MOV GATE27_EXT1, #0
    MOV GATE14_SUB3, #0
    MOV HANDSHAKE20_STATE, #0
    MOV TX_BUF+0, #0x63
    MOV TX_BUF+2, #0x6C
    MOV TX_LEN, #2
    CALLA UC, F_FRAME_TX
    RET

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

; 08_dtc_table.asm - tabela de 60 DTCs, agora DERIVADA por compilação real
; de reimplementacao_c/dtc/dtc_sirius32.{h,c} (via c167cc + port_real_abi.py),
; não mais reimplementada à mão em asm (ver histórico da versão anterior no
; git/notas - a versão hand-written simplificava os 4 arrays paralelos num
; único DTC_AGING; esta versão usa a struct real `dtc_sirius32_state_t`
; inteira, incluindo o array `dtc_sirius32_table` de 60 entradas completo).
;
; Igual antes: este firmware só chama clear()/decay_tick() (nenhum SID
; K-line de leitura/marcação de DTC existe nesta imagem - ver
; kline_dispatcher.c) - mark() está compilado e disponível (chamada real do
; C original), mas nenhum call site do firmware_min aciona ele, mesma
; honestidade documentada na versão anterior.
;
; Trampolins abaixo preservam a MESMA interface que o resto do firmware já
; usa (DTC_INIT/DTC_CLEAR(R0=modo)/DTC_DECAY_TICK, sem valor de retorno) -
; só a implementação por trás mudou, nenhum outro fragmento precisou ser
; editado.

DTC_INIT:
    MOV R4, #g_dtc_state
    CALLA UC, dtc_sirius32_state_init
    RET

DTC_CLEAR:
    MOV R4, #g_dtc_state
    MOV R5, R0
    CALLA UC, dtc_sirius32_clear
    RET

DTC_DECAY_TICK:
    MOV R4, #g_dtc_state
    CALLA UC, dtc_sirius32_decay_tick
    RET

; Generated by c167cc - do not edit by hand.
; Target: Siemens/Infineon C167CR
; See docs/assembly-syntax.md for the syntax and instruction reference.

dtc_sirius32_table:		DW	0,32768,1,0,0,0,1,1,1,16384,1,0,1,85,1,1,2,8192,1,0,2,64,1,2,3,4096,1,0,3,64,1,1,4,2048,1,0,4,24,1,1,5,1024,1,0,5,16,1,0,6,512,1,0,6,4,1,2,7,256,1,0,7,4,1,1,8,128,1,0,8,16,1,1,9,64,1,0,9,16,1,0,10,0,0,0,254,0,0,1,11,0,0,0,254,0,0,2,12,0,0,0,255,0,0,3,13,0,0,0,255,0,0,3,14,0,0,0,254,0,0,0,15,0,0,0,254,0,0,1,16,0,0,1,254,0,0,2,17,0,0,1,254,0,0,1,18,0,0,1,255,0,0,3,19,0,0,1,254,0,0,2,20,0,0,1,254,0,0,1,21,0,0,1,254,0,0,2,22,0,0,1,254,0,0,1,23,0,0,1,254,0,0,3,24,0,0,1,254,0,0,3,25,0,0,1,254,0,0,3,26,0,0,1,254,0,0,3,27,0,0,1,254,0,0,3,28,0,0,1,254,0,0,3,29,0,0,2,254,0,0,3,30,0,0,2,254,0,0,3,31,0,0,2,254,0,0,3,32,0,0,2,254,0,0,3,33,0,0,2,254,0,0,3,34,0,0,2,254,0,0,3,35,0,0,2,254,0,0,3,36,0,0,2,254,0,0,3,37,0,0,2,254,0,0,3,38,0,0,2,254,0,0,2,39,0,0,2,254,0,0,3,40,0,0,2,254,0,0,3,41,0,0,2,254,0,0,0,42,0,0,2,254,0,0,3,43,0,0,2,254,0,0,1,44,0,0,2,254,0,0,3,45,0,0,3,254,0,0,3,46,0,0,3,254,0,0,3,47,0,0,3,254,0,0,3,48,0,0,4,255,0,0,0,49,0,0,4,255,0,0,2,50,0,0,4,255,0,0,1,51,0,0,4,255,0,0,2,52,0,0,4,255,0,0,2,53,0,0,4,255,0,0,3,54,0,0,4,255,0,0,3,55,0,0,4,255,0,0,3,56,0,0,4,255,0,0,3,57,0,0,3,255,0,0,0,58,0,0,3,255,0,0,0,59,0,0,3,255,0,0,0		; array, inicializado
RESERVE g_dtc_state, #72


dtc_sirius32_get:
	PUSH     R15                 ; save caller's frame pointer
	SUB      SP, #4              ; allocate locals + spills
	MOV      R15, SP             ; establish frame pointer
	MOVB     [R15+#0], R4        ; spill incoming parameter 'index'
	; source: /tmp/dtc_fw_module.c:368
	MOVB     R0, [R15+#0]        ; R0 = index
	AND      R0, #0x00FF         
	MOV      R1, #60             
	CMP      R0, R1              
    JMPA NC, .Lcmp_true_1
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_1
.Lcmp_true_1:
	MOV      R2, #1              
.Lcmp_end_1:
	; source: /tmp/dtc_fw_module.c:369
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_get_if_then_0
    JMPA UC, .Ldtc_sirius32_get_if_end_2
.Ldtc_sirius32_get_if_then_0:
	; source: /tmp/dtc_fw_module.c:368
	MOV      R0, #0              
	ADD      SP, #4              ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET                          
.Ldtc_sirius32_get_if_end_2:
	; source: /tmp/dtc_fw_module.c:369
	MOV      R0, #dtc_sirius32_table; near address of global
	MOVB     R1, [R15+#0]        ; R1 = index
	AND      R1, #0x00FF         
	MOV      R2, #16             
	MUL     R1, R2              
	MOV      R3, MDL             ; low word of MDL:MDH product
	MOV      R1, R0              
	ADD      R1, R3              
	MOV      [R15+#2], R1        ; e = R1
	; source: /tmp/dtc_fw_module.c:370
	MOV      R0, [R15+#2]        ; R0 = e
	ADD      SP, #4              ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET                          

dtc_sirius32_state_init:
	PUSH     R15                 ; save caller's frame pointer
	SUB      SP, #6              ; allocate locals + spills
	MOV      R15, SP             ; establish frame pointer
	MOV      [R15+#0], R4        ; spill incoming parameter 'st'
	; source: /tmp/dtc_fw_module.c:378
	MOV      R0, #0              
	; source: /tmp/dtc_fw_module.c:381
	MOVB     [R15+#2], R0        ; w = R0
.Ldtc_sirius32_state_init_for_cond_0:
	; source: /tmp/dtc_fw_module.c:378
	MOVB     R0, [R15+#2]        ; R0 = w
	AND      R0, #0x00FF         
	MOV      R1, #5              
	CMP      R0, R1              
    JMPA C, .Lcmp_true_2
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_2
.Lcmp_true_2:
	MOV      R2, #1              
.Lcmp_end_2:
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_state_init_for_body_1
    JMPA UC, .Ldtc_sirius32_state_init_for_end_3
.Ldtc_sirius32_state_init_for_body_1:
	; source: /tmp/dtc_fw_module.c:379
	MOV      R0, #0              
	MOV      R1, [R15+#0]        ; R1 = st
	MOVB     R2, [R15+#2]        ; R2 = w
	AND      R2, #0x00FF         
	MOV      R3, #2              
	MUL     R2, R3              
	MOV      R8, MDL             ; low word of MDL:MDH product
	MOV      R2, R1              
	ADD      R2, R8              
	MOV      [R2], R0            
	; source: /tmp/dtc_fw_module.c:380
	MOV      R0, #0              
	MOV      R1, [R15+#0]        ; R1 = st
	MOV      R2, #10             
	MOV      R3, R1              
	ADD      R3, R2              
	MOVB     R1, [R15+#2]        ; R1 = w
	AND      R1, #0x00FF         
	MOV      R2, #2              
	MUL     R1, R2              
	MOV      R8, MDL             ; low word of MDL:MDH product
	MOV      R1, R3              
	ADD      R1, R8              
	MOV      [R1], R0            
.Ldtc_sirius32_state_init_for_post_2:
	; source: /tmp/dtc_fw_module.c:378
	MOVB     R0, [R15+#2]        ; R0 = w
	AND      R0, #0x00FF         
	MOV      R1, #1              
	MOV      R2, R0              
	ADD      R2, R1              
	MOVB     [R15+#2], R2        ; w = R2
    JMPA UC, .Ldtc_sirius32_state_init_for_cond_0
.Ldtc_sirius32_state_init_for_end_3:
	; source: /tmp/dtc_fw_module.c:382
	MOV      R0, #0              
	; source: /tmp/dtc_fw_module.c:384
	MOVB     [R15+#4], R0        ; a = R0
.Ldtc_sirius32_state_init_for_cond_4:
	; source: /tmp/dtc_fw_module.c:382
	MOVB     R0, [R15+#4]        ; R0 = a
	AND      R0, #0x00FF         
	MOV      R1, #46             
	CMP      R0, R1              
    JMPA C, .Lcmp_true_3
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_3
.Lcmp_true_3:
	MOV      R2, #1              
.Lcmp_end_3:
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_state_init_for_body_5
    JMPA UC, .Ldtc_sirius32_state_init_for_end_7
.Ldtc_sirius32_state_init_for_body_5:
	; source: /tmp/dtc_fw_module.c:383
	MOV      R0, #0              
	MOV      R1, [R15+#0]        ; R1 = st
	MOV      R2, #20             
	MOV      R3, R1              
	ADD      R3, R2              
	MOVB     R1, [R15+#4]        ; R1 = a
	AND      R1, #0x00FF         
	MOV      R2, R3              
	ADD      R2, R1              
	MOVB     [R2], R0            
.Ldtc_sirius32_state_init_for_post_6:
	; source: /tmp/dtc_fw_module.c:382
	MOVB     R0, [R15+#4]        ; R0 = a
	AND      R0, #0x00FF         
	MOV      R1, #1              
	MOV      R2, R0              
	ADD      R2, R1              
	MOVB     [R15+#4], R2        ; a = R2
    JMPA UC, .Ldtc_sirius32_state_init_for_cond_4
.Ldtc_sirius32_state_init_for_end_7:
	; source: /tmp/dtc_fw_module.c:385
	MOV      R0, #0              
	MOV      R1, [R15+#0]        ; R1 = st
	MOV      R2, #68             
	MOV      R3, R1              
	ADD      R3, R2              
	MOV      [R3], R0            
	; source: /tmp/dtc_fw_module.c:386
	MOV      R0, #0              
	MOV      R1, [R15+#0]        ; R1 = st
	MOV      R2, #70             
	MOV      R3, R1              
	ADD      R3, R2              
	MOV      [R3], R0            
	; source: /tmp/dtc_fw_module.c:393
	MOV      R0, #0              
	MOV      R1, [R15+#0]        ; R1 = st
	MOV      R2, #66             
	MOV      R3, R1              
	ADD      R3, R2              
	MOV      [R3], R0            
	ADD      SP, #6              ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET                          

dtc_sirius32_mark:
	PUSH     R15                 ; save caller's frame pointer
	SUB      SP, #12             ; allocate locals + spills
	MOV      R15, SP             ; establish frame pointer
	MOV      [R15+#0], R4        ; spill incoming parameter 'st'
	MOVB     [R15+#2], R5        ; spill incoming parameter 'index'
	MOV      [R15+#4], R6        ; spill incoming parameter 'set'
	; source: /tmp/dtc_fw_module.c:398
	MOVB     R0, [R15+#2]        ; R0 = index
	AND      R0, #0x00FF         
	MOV      R4, R0              
    CALLA UC, dtc_sirius32_get
	MOV      R1, R0              ; function result
	MOV      [R15+#6], R1        ; e = R1
	; source: /tmp/dtc_fw_module.c:399
	MOV      R0, [R15+#6]        ; R0 = e
	CMP      R0, #0              
    JMPA Z, .Lnot_true_1
	MOV      R1, #0              
    JMPA UC, .Lnot_end_1
.Lnot_true_1:
	MOV      R1, #1              
.Lnot_end_1:
	; source: /tmp/dtc_fw_module.c:401
	CMP      R1, #0              
    JMPA NZ, .Ldtc_sirius32_mark_if_then_0
    JMPA UC, .Ldtc_sirius32_mark_if_end_2
.Ldtc_sirius32_mark_if_then_0:
	; source: /tmp/dtc_fw_module.c:399
	MOV      R0, #0              
	ADD      SP, #12             ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET                          
.Ldtc_sirius32_mark_if_end_2:
	; source: /tmp/dtc_fw_module.c:401
	MOV      R0, [R15+#4]        ; R0 = set
	CMP      R0, #0              
    JMPA Z, .Lnot_true_2
	MOV      R1, #0              
    JMPA UC, .Lnot_end_2
.Lnot_true_2:
	MOV      R1, #1              
.Lnot_end_2:
	; source: /tmp/dtc_fw_module.c:409
	CMP      R1, #0              
    JMPA NZ, .Ldtc_sirius32_mark_if_then_3
    JMPA UC, .Ldtc_sirius32_mark_if_end_5
.Ldtc_sirius32_mark_if_then_3:
	; source: /tmp/dtc_fw_module.c:403
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #4              
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	CMP      R0, #0              
    JMPA Z, .Lnot_true_3
	MOV      R1, #0              
    JMPA UC, .Lnot_end_3
.Lnot_true_3:
	MOV      R1, #1              
.Lnot_end_3:
	; source: /tmp/dtc_fw_module.c:404
	CMP      R1, #0              
    JMPA NZ, .Ldtc_sirius32_mark_if_then_6
    JMPA UC, .Ldtc_sirius32_mark_if_end_8
.Ldtc_sirius32_mark_if_then_6:
	; source: /tmp/dtc_fw_module.c:403
	MOV      R0, #0              
	ADD      SP, #12             ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET                          
.Ldtc_sirius32_mark_if_end_8:
	; source: /tmp/dtc_fw_module.c:404
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #2              
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	MOV      R1, R0              
	CPL      R1                  
	MOV      R0, [R15+#0]        ; R0 = st
	MOV      R2, [R15+#6]        ; R2 = e
	MOV      R3, #6              
	MOV      R8, R2              
	ADD      R8, R3              
	MOVB     R2, [R8]            
	AND      R2, #0x00FF         
	MOV      R3, #2              
	MUL     R2, R3              
	MOV      R8, MDL             ; low word of MDL:MDH product
	MOV      R2, R0              
	ADD      R2, R8              
	MOV      R0, [R2]            
	MOV      R2, R0              
	AND      R2, R1              
	MOV      R0, [R15+#0]        ; R0 = st
	MOV      R1, [R15+#6]        ; R1 = e
	MOV      R3, #6              
	MOV      R8, R1              
	ADD      R8, R3              
	MOVB     R1, [R8]            
	AND      R1, #0x00FF         
	MOV      R3, #2              
	MUL     R1, R3              
	MOV      R8, MDL             ; low word of MDL:MDH product
	MOV      R1, R0              
	ADD      R1, R8              
	MOV      [R1], R2            
	; source: /tmp/dtc_fw_module.c:405
	MOV      R0, #1              
	ADD      SP, #12             ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET                          
.Ldtc_sirius32_mark_if_end_5:
	; source: /tmp/dtc_fw_module.c:409
	MOV      R0, #0              
	MOV      [R15+#8], R0        ; touched_status = R0
	; source: /tmp/dtc_fw_module.c:410
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #4              
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	; source: /tmp/dtc_fw_module.c:415
	CMP      R0, #0              
    JMPA NZ, .Ldtc_sirius32_mark_if_then_9
    JMPA UC, .Ldtc_sirius32_mark_if_end_11
.Ldtc_sirius32_mark_if_then_9:
	; source: /tmp/dtc_fw_module.c:411
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #2              
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	MOV      R1, [R15+#0]        ; R1 = st
	MOV      R2, [R15+#6]        ; R2 = e
	MOV      R3, #6              
	MOV      R8, R2              
	ADD      R8, R3              
	MOVB     R2, [R8]            
	AND      R2, #0x00FF         
	MOV      R3, #2              
	MUL     R2, R3              
	MOV      R8, MDL             ; low word of MDL:MDH product
	MOV      R2, R1              
	ADD      R2, R8              
	MOV      R1, [R2]            
	MOV      R2, R1              
	OR       R2, R0              
	MOV      R0, [R15+#0]        ; R0 = st
	MOV      R1, [R15+#6]        ; R1 = e
	MOV      R3, #6              
	MOV      R8, R1              
	ADD      R8, R3              
	MOVB     R1, [R8]            
	AND      R1, #0x00FF         
	MOV      R3, #2              
	MUL     R1, R3              
	MOV      R8, MDL             ; low word of MDL:MDH product
	MOV      R1, R0              
	ADD      R1, R8              
	MOV      [R1], R2            
	; source: /tmp/dtc_fw_module.c:412
	MOV      R0, #1              
	MOV      [R15+#8], R0        ; touched_status = R0
.Ldtc_sirius32_mark_if_end_11:
	; source: /tmp/dtc_fw_module.c:415
	MOV      R0, [R15+#0]        ; R0 = st
	MOV      R1, #66             
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	; source: /tmp/dtc_fw_module.c:435
	CMP      R0, #0              
    JMPA NZ, .Ldtc_sirius32_mark_if_then_12
    JMPA UC, .Ldtc_sirius32_mark_if_end_14
.Ldtc_sirius32_mark_if_then_12:
	; source: /tmp/dtc_fw_module.c:418
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #8              
	MOV      R2, R0              
	ADD      R2, R1              
	MOVB     R0, [R2]            
	AND      R0, #0x00FF         
	MOV      R1, #255            
	CMP      R0, R1              
    JMPA NZ, .Lcmp_true_4
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_4
.Lcmp_true_4:
	MOV      R2, #1              
.Lcmp_end_4:
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_mark_and_rhs_22
    JMPA UC, .Ldtc_sirius32_mark_logic_false_24
.Ldtc_sirius32_mark_and_rhs_22:
	; source: /tmp/dtc_fw_module.c:419
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #8              
	MOV      R2, R0              
	ADD      R2, R1              
	MOVB     R0, [R2]            
	AND      R0, #0x00FF         
	MOV      R1, #46             
	CMP      R0, R1              
    JMPA C, .Lcmp_true_5
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_5
.Lcmp_true_5:
	MOV      R2, #1              
.Lcmp_end_5:
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_mark_logic_true_23
    JMPA UC, .Ldtc_sirius32_mark_logic_false_24
.Ldtc_sirius32_mark_logic_true_23:
	MOV      R0, #1              
    JMPA UC, .Ldtc_sirius32_mark_logic_end_25
.Ldtc_sirius32_mark_logic_false_24:
	MOV      R0, #0              
.Ldtc_sirius32_mark_logic_end_25:
	CMP      R0, #0              
    JMPA NZ, .Ldtc_sirius32_mark_and_rhs_18
    JMPA UC, .Ldtc_sirius32_mark_logic_false_20
.Ldtc_sirius32_mark_and_rhs_18:
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #12             
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	CMP      R0, #0              
    JMPA NZ, .Ldtc_sirius32_mark_logic_true_19
    JMPA UC, .Ldtc_sirius32_mark_logic_false_20
.Ldtc_sirius32_mark_logic_true_19:
	MOV      R0, #1              
    JMPA UC, .Ldtc_sirius32_mark_logic_end_21
.Ldtc_sirius32_mark_logic_false_20:
	MOV      R0, #0              
.Ldtc_sirius32_mark_logic_end_21:
	; source: /tmp/dtc_fw_module.c:428
	CMP      R0, #0              
    JMPA NZ, .Ldtc_sirius32_mark_if_then_15
    JMPA UC, .Ldtc_sirius32_mark_if_else_16
.Ldtc_sirius32_mark_if_then_15:
	; source: /tmp/dtc_fw_module.c:420
	MOV      R0, [R15+#0]        ; R0 = st
	MOV      R1, #20             
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #8              
	MOV      R3, R0              
	ADD      R3, R1              
	MOVB     R0, [R3]            
	AND      R0, #0x00FF         
	MOV      R1, R2              
	ADD      R1, R0              
	MOVB     R0, [R1]            
	AND      R0, #0x00FF         
	MOV      R1, [R15+#6]        ; R1 = e
	MOV      R2, #10             
	MOV      R3, R1              
	ADD      R3, R2              
	MOVB     R1, [R3]            
	AND      R1, #0x00FF         
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      [R15+#10], R2       ; sum = R2
	; source: /tmp/dtc_fw_module.c:421
	MOV      R0, [R15+#10]       ; R0 = sum
	MOV      R1, #255            
	CMP      R0, R1              
    JMPA UGT, .Lcmp_true_6
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_6
.Lcmp_true_6:
	MOV      R2, #1              
.Lcmp_end_6:
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_mark_tern_true_26
    JMPA UC, .Ldtc_sirius32_mark_tern_false_27
.Ldtc_sirius32_mark_tern_true_26:
	MOV      R0, #255            
	MOV      R1, R0              
    JMPA UC, .Ldtc_sirius32_mark_tern_end_28
.Ldtc_sirius32_mark_tern_false_27:
	MOV      R0, [R15+#10]       ; R0 = sum
	MOV      R1, R0              
.Ldtc_sirius32_mark_tern_end_28:
	MOV      R0, [R15+#0]        ; R0 = st
	MOV      R2, #20             
	MOV      R3, R0              
	ADD      R3, R2              
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R2, #8              
	MOV      R8, R0              
	ADD      R8, R2              
	MOVB     R0, [R8]            
	AND      R0, #0x00FF         
	MOV      R2, R3              
	ADD      R2, R0              
	MOVB     [R2], R1            
    JMPA UC, .Ldtc_sirius32_mark_if_end_17
.Ldtc_sirius32_mark_if_else_16:
	; source: /tmp/dtc_fw_module.c:422
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #8              
	MOV      R2, R0              
	ADD      R2, R1              
	MOVB     R0, [R2]            
	AND      R0, #0x00FF         
	MOV      R1, #255            
	CMP      R0, R1              
    JMPA Z, .Lcmp_true_7
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_7
.Lcmp_true_7:
	MOV      R2, #1              
.Lcmp_end_7:
	; source: /tmp/dtc_fw_module.c:428
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_mark_if_then_29
    JMPA UC, .Ldtc_sirius32_mark_if_end_31
.Ldtc_sirius32_mark_if_then_29:
.Ldtc_sirius32_mark_if_end_31:
.Ldtc_sirius32_mark_if_end_17:
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #4              
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	; source: /tmp/dtc_fw_module.c:433
	CMP      R0, #0              
    JMPA NZ, .Ldtc_sirius32_mark_if_then_32
    JMPA UC, .Ldtc_sirius32_mark_if_end_34
.Ldtc_sirius32_mark_if_then_32:
	; source: /tmp/dtc_fw_module.c:429
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #2              
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	MOV      R1, [R15+#0]        ; R1 = st
	MOV      R2, #10             
	MOV      R3, R1              
	ADD      R3, R2              
	MOV      R1, [R15+#6]        ; R1 = e
	MOV      R2, #6              
	MOV      R8, R1              
	ADD      R8, R2              
	MOVB     R1, [R8]            
	AND      R1, #0x00FF         
	MOV      R2, #2              
	MUL     R1, R2              
	MOV      R8, MDL             ; low word of MDL:MDH product
	MOV      R1, R3              
	ADD      R1, R8              
	MOV      R2, [R1]            
	MOV      R1, R2              
	OR       R1, R0              
	MOV      R0, [R15+#0]        ; R0 = st
	MOV      R2, #10             
	MOV      R3, R0              
	ADD      R3, R2              
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R2, #6              
	MOV      R8, R0              
	ADD      R8, R2              
	MOVB     R0, [R8]            
	AND      R0, #0x00FF         
	MOV      R2, #2              
	MUL     R0, R2              
	MOV      R8, MDL             ; low word of MDL:MDH product
	MOV      R0, R3              
	ADD      R0, R8              
	MOV      [R0], R1            
	; source: /tmp/dtc_fw_module.c:430
	MOV      R0, #1              
	MOV      [R15+#8], R0        ; touched_status = R0
	; source: /tmp/dtc_fw_module.c:431
	MOV      R0, [R15+#0]        ; R0 = st
	MOV      R1, #68             
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	; source: /tmp/dtc_fw_module.c:432
	CMP      R0, #0              
    JMPA NZ, .Ldtc_sirius32_mark_if_then_35
    JMPA UC, .Ldtc_sirius32_mark_if_end_37
.Ldtc_sirius32_mark_if_then_35:
	; source: /tmp/dtc_fw_module.c:431
	MOV      R0, [R15+#0]        ; R0 = st
	MOV      R1, #68             
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	MOVB     R1, [R15+#2]        ; R1 = index
	AND      R1, #0x00FF         
	MOV      R2, [R15+#0]        ; R2 = st
	MOV      R3, #70             
	MOV      R8, R2              
	ADD      R8, R3              
	MOV      R2, [R8]            
	MOV      R4, R1              
	MOV      R5, R2              
	CALLI    cc_UC, R0           
	MOV      R3, R0              ; function result
.Ldtc_sirius32_mark_if_end_37:
.Ldtc_sirius32_mark_if_end_34:
.Ldtc_sirius32_mark_if_end_14:
	; source: /tmp/dtc_fw_module.c:435
	MOV      R0, [R15+#8]        ; R0 = touched_status
	ADD      SP, #12             ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET                          

dtc_sirius32_decay_tick:
	PUSH     R15                 ; save caller's frame pointer
	SUB      SP, #8              ; allocate locals + spills
	MOV      R15, SP             ; establish frame pointer
	MOV      [R15+#0], R4        ; spill incoming parameter 'st'
	; source: /tmp/dtc_fw_module.c:454
	MOV      R0, #0              
	; source: /tmp/dtc_fw_module.c:467
	MOVB     [R15+#2], R0        ; i = R0
.Ldtc_sirius32_decay_tick_for_cond_0:
	; source: /tmp/dtc_fw_module.c:454
	MOVB     R0, [R15+#2]        ; R0 = i
	AND      R0, #0x00FF         
	MOV      R1, #60             
	CMP      R0, R1              
    JMPA C, .Lcmp_true_8
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_8
.Lcmp_true_8:
	MOV      R2, #1              
.Lcmp_end_8:
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_decay_tick_for_body_1
    JMPA UC, .Ldtc_sirius32_decay_tick_for_end_3
.Ldtc_sirius32_decay_tick_for_body_1:
	; source: /tmp/dtc_fw_module.c:455
	MOV      R0, #dtc_sirius32_table; near address of global
	MOVB     R1, [R15+#2]        ; R1 = i
	AND      R1, #0x00FF         
	MOV      R2, #16             
	MUL     R1, R2              
	MOV      R3, MDL             ; low word of MDL:MDH product
	MOV      R1, R0              
	ADD      R1, R3              
	MOV      [R15+#4], R1        ; e = R1
	; source: /tmp/dtc_fw_module.c:456
	MOV      R0, [R15+#4]        ; R0 = e
	MOV      R1, #8              
	MOV      R2, R0              
	ADD      R2, R1              
	MOVB     R0, [R2]            
	AND      R0, #0x00FF         
	MOV      R1, #255            
	CMP      R0, R1              
    JMPA Z, .Lcmp_true_9
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_9
.Lcmp_true_9:
	MOV      R2, #1              
.Lcmp_end_9:
	; source: /tmp/dtc_fw_module.c:457
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_decay_tick_if_then_4
    JMPA UC, .Ldtc_sirius32_decay_tick_if_end_6
.Ldtc_sirius32_decay_tick_if_then_4:
    JMPA UC, .Ldtc_sirius32_decay_tick_for_post_2
.Ldtc_sirius32_decay_tick_if_end_6:
	MOV      R0, [R15+#4]        ; R0 = e
	MOV      R1, #8              
	MOV      R2, R0              
	ADD      R2, R1              
	MOVB     R0, [R2]            
	AND      R0, #0x00FF         
	MOV      R1, #46             
	CMP      R0, R1              
    JMPA NC, .Lcmp_true_10
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_10
.Lcmp_true_10:
	MOV      R2, #1              
.Lcmp_end_10:
	; source: /tmp/dtc_fw_module.c:458
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_decay_tick_if_then_7
    JMPA UC, .Ldtc_sirius32_decay_tick_if_end_9
.Ldtc_sirius32_decay_tick_if_then_7:
    JMPA UC, .Ldtc_sirius32_decay_tick_for_post_2
.Ldtc_sirius32_decay_tick_if_end_9:
	MOV      R0, [R15+#4]        ; R0 = e
	MOV      R1, #12             
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	CMP      R0, #0              
    JMPA Z, .Lnot_true_4
	MOV      R1, #0              
    JMPA UC, .Lnot_end_4
.Lnot_true_4:
	MOV      R1, #1              
.Lnot_end_4:
	; source: /tmp/dtc_fw_module.c:460
	CMP      R1, #0              
    JMPA NZ, .Ldtc_sirius32_decay_tick_if_then_10
    JMPA UC, .Ldtc_sirius32_decay_tick_if_end_12
.Ldtc_sirius32_decay_tick_if_then_10:
    JMPA UC, .Ldtc_sirius32_decay_tick_for_post_2
.Ldtc_sirius32_decay_tick_if_end_12:
	MOV      R0, [R15+#0]        ; R0 = st
	MOV      R1, #20             
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R15+#4]        ; R0 = e
	MOV      R1, #8              
	MOV      R3, R0              
	ADD      R3, R1              
	MOVB     R0, [R3]            
	AND      R0, #0x00FF         
	MOV      R1, R2              
	ADD      R1, R0              
	MOVB     R0, [R1]            
	AND      R0, #0x00FF         
	MOV      R1, [R15+#4]        ; R1 = e
	MOV      R2, #10             
	MOV      R3, R1              
	ADD      R3, R2              
	MOVB     R1, [R3]            
	AND      R1, #0x00FF         
	MOV      R2, R0              
	SUB      R2, R1              
	MOV      [R15+#6], R2        ; v = R2
	; source: /tmp/dtc_fw_module.c:461
	MOV      R0, [R15+#6]        ; R0 = v
	MOV      R1, #0              
	CMP      R0, R1              
    JMPA SLT, .Lcmp_true_11
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_11
.Lcmp_true_11:
	MOV      R2, #1              
.Lcmp_end_11:
	; source: /tmp/dtc_fw_module.c:462
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_decay_tick_if_then_13
    JMPA UC, .Ldtc_sirius32_decay_tick_if_end_15
.Ldtc_sirius32_decay_tick_if_then_13:
	; source: /tmp/dtc_fw_module.c:461
	MOV      R0, #0              
	MOV      [R15+#6], R0        ; v = R0
.Ldtc_sirius32_decay_tick_if_end_15:
	; source: /tmp/dtc_fw_module.c:462
	MOV      R0, [R15+#6]        ; R0 = v
	MOV      R1, [R15+#0]        ; R1 = st
	MOV      R2, #20             
	MOV      R3, R1              
	ADD      R3, R2              
	MOV      R1, [R15+#4]        ; R1 = e
	MOV      R2, #8              
	MOV      R8, R1              
	ADD      R8, R2              
	MOVB     R1, [R8]            
	AND      R1, #0x00FF         
	MOV      R2, R3              
	ADD      R2, R1              
	MOVB     [R2], R0            
	; source: /tmp/dtc_fw_module.c:464
	MOV      R0, [R15+#6]        ; R0 = v
	MOV      R1, #128            
	CMP      R0, R1              
    JMPA SLT, .Lcmp_true_12
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_12
.Lcmp_true_12:
	MOV      R2, #1              
.Lcmp_end_12:
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_decay_tick_and_rhs_19
    JMPA UC, .Ldtc_sirius32_decay_tick_logic_false_21
.Ldtc_sirius32_decay_tick_and_rhs_19:
	MOV      R0, [R15+#4]        ; R0 = e
	MOV      R1, #4              
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	CMP      R0, #0              
    JMPA NZ, .Ldtc_sirius32_decay_tick_logic_true_20
    JMPA UC, .Ldtc_sirius32_decay_tick_logic_false_21
.Ldtc_sirius32_decay_tick_logic_true_20:
	MOV      R0, #1              
    JMPA UC, .Ldtc_sirius32_decay_tick_logic_end_22
.Ldtc_sirius32_decay_tick_logic_false_21:
	MOV      R0, #0              
.Ldtc_sirius32_decay_tick_logic_end_22:
	; source: /tmp/dtc_fw_module.c:467
	CMP      R0, #0              
    JMPA NZ, .Ldtc_sirius32_decay_tick_if_then_16
    JMPA UC, .Ldtc_sirius32_decay_tick_if_end_18
.Ldtc_sirius32_decay_tick_if_then_16:
	; source: /tmp/dtc_fw_module.c:465
	MOV      R0, [R15+#4]        ; R0 = e
	MOV      R1, #2              
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	MOV      R1, R0              
	CPL      R1                  
	MOV      R0, [R15+#0]        ; R0 = st
	MOV      R2, #10             
	MOV      R3, R0              
	ADD      R3, R2              
	MOV      R0, [R15+#4]        ; R0 = e
	MOV      R2, #6              
	MOV      R8, R0              
	ADD      R8, R2              
	MOVB     R0, [R8]            
	AND      R0, #0x00FF         
	MOV      R2, #2              
	MUL     R0, R2              
	MOV      R8, MDL             ; low word of MDL:MDH product
	MOV      R0, R3              
	ADD      R0, R8              
	MOV      R2, [R0]            
	MOV      R0, R2              
	AND      R0, R1              
	MOV      R1, [R15+#0]        ; R1 = st
	MOV      R2, #10             
	MOV      R3, R1              
	ADD      R3, R2              
	MOV      R1, [R15+#4]        ; R1 = e
	MOV      R2, #6              
	MOV      R8, R1              
	ADD      R8, R2              
	MOVB     R1, [R8]            
	AND      R1, #0x00FF         
	MOV      R2, #2              
	MUL     R1, R2              
	MOV      R8, MDL             ; low word of MDL:MDH product
	MOV      R1, R3              
	ADD      R1, R8              
	MOV      [R1], R0            
.Ldtc_sirius32_decay_tick_if_end_18:
.Ldtc_sirius32_decay_tick_for_post_2:
	; source: /tmp/dtc_fw_module.c:454
	MOVB     R0, [R15+#2]        ; R0 = i
	AND      R0, #0x00FF         
	MOV      R1, #1              
	MOV      R2, R0              
	ADD      R2, R1              
	MOVB     [R15+#2], R2        ; i = R2
    JMPA UC, .Ldtc_sirius32_decay_tick_for_cond_0
.Ldtc_sirius32_decay_tick_for_end_3:
	ADD      SP, #8              ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET                          

dtc_sirius32_clear:
	PUSH     R15                 ; save caller's frame pointer
	SUB      SP, #12             ; allocate locals + spills
	MOV      R15, SP             ; establish frame pointer
	MOV      [R15+#0], R4        ; spill incoming parameter 'st'
	MOV      [R15+#2], R5        ; spill incoming parameter 'mode'
	; source: /tmp/dtc_fw_module.c:473
	MOV      R0, [R15+#2]        ; R0 = mode
	MOV      R1, #2              
	CMP      R0, R1              
    JMPA Z, .Lcmp_true_13
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_13
.Lcmp_true_13:
	MOV      R2, #1              
.Lcmp_end_13:
	; source: /tmp/dtc_fw_module.c:493
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_clear_if_then_0
    JMPA UC, .Ldtc_sirius32_clear_if_end_2
.Ldtc_sirius32_clear_if_then_0:
	; source: /tmp/dtc_fw_module.c:474
	MOV      R0, #0              
	; source: /tmp/dtc_fw_module.c:483
	MOVB     [R15+#4], R0        ; i = R0
.Ldtc_sirius32_clear_for_cond_3:
	; source: /tmp/dtc_fw_module.c:474
	MOVB     R0, [R15+#4]        ; R0 = i
	AND      R0, #0x00FF         
	MOV      R1, #60             
	CMP      R0, R1              
    JMPA C, .Lcmp_true_14
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_14
.Lcmp_true_14:
	MOV      R2, #1              
.Lcmp_end_14:
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_clear_for_body_4
    JMPA UC, .Ldtc_sirius32_clear_for_end_6
.Ldtc_sirius32_clear_for_body_4:
	; source: /tmp/dtc_fw_module.c:475
	MOV      R0, #dtc_sirius32_table; near address of global
	MOVB     R1, [R15+#4]        ; R1 = i
	AND      R1, #0x00FF         
	MOV      R2, #16             
	MUL     R1, R2              
	MOV      R3, MDL             ; low word of MDL:MDH product
	MOV      R1, R0              
	ADD      R1, R3              
	MOV      [R15+#6], R1        ; e = R1
	; source: /tmp/dtc_fw_module.c:476
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #4              
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	CMP      R0, #0              
    JMPA Z, .Lnot_true_5
	MOV      R1, #0              
    JMPA UC, .Lnot_end_5
.Lnot_true_5:
	MOV      R1, #1              
.Lnot_end_5:
	; source: /tmp/dtc_fw_module.c:477
	CMP      R1, #0              
    JMPA NZ, .Ldtc_sirius32_clear_if_then_7
    JMPA UC, .Ldtc_sirius32_clear_if_end_9
.Ldtc_sirius32_clear_if_then_7:
    JMPA UC, .Ldtc_sirius32_clear_for_post_5
.Ldtc_sirius32_clear_if_end_9:
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #8              
	MOV      R2, R0              
	ADD      R2, R1              
	MOVB     R0, [R2]            
	AND      R0, #0x00FF         
	MOV      R1, #255            
	CMP      R0, R1              
    JMPA Z, .Lcmp_true_15
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_15
.Lcmp_true_15:
	MOV      R2, #1              
.Lcmp_end_15:
	; source: /tmp/dtc_fw_module.c:478
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_clear_if_then_10
    JMPA UC, .Ldtc_sirius32_clear_if_end_12
.Ldtc_sirius32_clear_if_then_10:
    JMPA UC, .Ldtc_sirius32_clear_for_post_5
.Ldtc_sirius32_clear_if_end_12:
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #8              
	MOV      R2, R0              
	ADD      R2, R1              
	MOVB     R0, [R2]            
	AND      R0, #0x00FF         
	MOV      R1, #46             
	CMP      R0, R1              
    JMPA NC, .Lcmp_true_16
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_16
.Lcmp_true_16:
	MOV      R2, #1              
.Lcmp_end_16:
	; source: /tmp/dtc_fw_module.c:479
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_clear_if_then_13
    JMPA UC, .Ldtc_sirius32_clear_if_end_15
.Ldtc_sirius32_clear_if_then_13:
    JMPA UC, .Ldtc_sirius32_clear_for_post_5
.Ldtc_sirius32_clear_if_end_15:
	MOV      R0, [R15+#0]        ; R0 = st
	MOV      R1, #10             
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R1, #6              
	MOV      R3, R0              
	ADD      R3, R1              
	MOVB     R0, [R3]            
	AND      R0, #0x00FF         
	MOV      R1, #2              
	MUL     R0, R1              
	MOV      R3, MDL             ; low word of MDL:MDH product
	MOV      R0, R2              
	ADD      R0, R3              
	MOV      R1, [R0]            
	MOV      R0, [R15+#6]        ; R0 = e
	MOV      R2, #2              
	MOV      R3, R0              
	ADD      R3, R2              
	MOV      R0, [R3]            
	MOV      R2, R1              
	AND      R2, R0              
	MOV      R0, #0              
	CMP      R2, R0              
    JMPA NZ, .Lcmp_true_17
	MOV      R1, #0              
    JMPA UC, .Lcmp_end_17
.Lcmp_true_17:
	MOV      R1, #1              
.Lcmp_end_17:
	MOV      [R15+#8], R1        ; is_confirmed = R1
	; source: /tmp/dtc_fw_module.c:480
	MOV      R0, [R15+#8]        ; R0 = is_confirmed
	; source: /tmp/dtc_fw_module.c:483
	CMP      R0, #0              
    JMPA NZ, .Ldtc_sirius32_clear_if_then_16
    JMPA UC, .Ldtc_sirius32_clear_if_end_18
.Ldtc_sirius32_clear_if_then_16:
	; source: /tmp/dtc_fw_module.c:481
	MOV      R0, #0              
	MOV      R1, [R15+#0]        ; R1 = st
	MOV      R2, #20             
	MOV      R3, R1              
	ADD      R3, R2              
	MOV      R1, [R15+#6]        ; R1 = e
	MOV      R2, #8              
	MOV      R8, R1              
	ADD      R8, R2              
	MOVB     R1, [R8]            
	AND      R1, #0x00FF         
	MOV      R2, R3              
	ADD      R2, R1              
	MOVB     [R2], R0            
.Ldtc_sirius32_clear_if_end_18:
.Ldtc_sirius32_clear_for_post_5:
	; source: /tmp/dtc_fw_module.c:474
	MOVB     R0, [R15+#4]        ; R0 = i
	AND      R0, #0x00FF         
	MOV      R1, #1              
	MOV      R2, R0              
	ADD      R2, R1              
	MOVB     [R15+#4], R2        ; i = R2
    JMPA UC, .Ldtc_sirius32_clear_for_cond_3
.Ldtc_sirius32_clear_for_end_6:
	; source: /tmp/dtc_fw_module.c:484
	ADD      SP, #12             ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET                          
.Ldtc_sirius32_clear_if_end_2:
	; source: /tmp/dtc_fw_module.c:493
	MOV      R0, #0              
	; source: /tmp/dtc_fw_module.c:495
	MOVB     [R15+#10], R0       ; a = R0
.Ldtc_sirius32_clear_for_cond_19:
	; source: /tmp/dtc_fw_module.c:493
	MOVB     R0, [R15+#10]       ; R0 = a
	AND      R0, #0x00FF         
	MOV      R1, #46             
	CMP      R0, R1              
    JMPA C, .Lcmp_true_18
	MOV      R2, #0              
    JMPA UC, .Lcmp_end_18
.Lcmp_true_18:
	MOV      R2, #1              
.Lcmp_end_18:
	CMP      R2, #0              
    JMPA NZ, .Ldtc_sirius32_clear_for_body_20
    JMPA UC, .Ldtc_sirius32_clear_for_end_22
.Ldtc_sirius32_clear_for_body_20:
	; source: /tmp/dtc_fw_module.c:494
	MOV      R0, #0              
	MOV      R1, [R15+#0]        ; R1 = st
	MOV      R2, #20             
	MOV      R3, R1              
	ADD      R3, R2              
	MOVB     R1, [R15+#10]       ; R1 = a
	AND      R1, #0x00FF         
	MOV      R2, R3              
	ADD      R2, R1              
	MOVB     [R2], R0            
.Ldtc_sirius32_clear_for_post_21:
	; source: /tmp/dtc_fw_module.c:493
	MOVB     R0, [R15+#10]       ; R0 = a
	AND      R0, #0x00FF         
	MOV      R1, #1              
	MOV      R2, R0              
	ADD      R2, R1              
	MOVB     [R15+#10], R2       ; a = R2
    JMPA UC, .Ldtc_sirius32_clear_for_cond_19
.Ldtc_sirius32_clear_for_end_22:
	ADD      SP, #12             ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET                          

dtc_sirius32_is_pending:
	PUSH     R15                 ; save caller's frame pointer
	SUB      SP, #6              ; allocate locals + spills
	MOV      R15, SP             ; establish frame pointer
	MOV      [R15+#0], R4        ; spill incoming parameter 'st'
	MOVB     [R15+#2], R5        ; spill incoming parameter 'index'
	; source: /tmp/dtc_fw_module.c:500
	MOVB     R0, [R15+#2]        ; R0 = index
	AND      R0, #0x00FF         
	MOV      R4, R0              
    CALLA UC, dtc_sirius32_get
	MOV      R1, R0              ; function result
	MOV      [R15+#4], R1        ; e = R1
	; source: /tmp/dtc_fw_module.c:501
	MOV      R0, [R15+#4]        ; R0 = e
	CMP      R0, #0              
    JMPA Z, .Lnot_true_6
	MOV      R1, #0              
    JMPA UC, .Lnot_end_6
.Lnot_true_6:
	MOV      R1, #1              
.Lnot_end_6:
	CMP      R1, #0              
    JMPA NZ, .Ldtc_sirius32_is_pending_logic_true_4
.Ldtc_sirius32_is_pending_or_rhs_3:
	MOV      R0, [R15+#4]        ; R0 = e
	MOV      R1, #4              
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	CMP      R0, #0              
    JMPA Z, .Lnot_true_7
	MOV      R1, #0              
    JMPA UC, .Lnot_end_7
.Lnot_true_7:
	MOV      R1, #1              
.Lnot_end_7:
	CMP      R1, #0              
    JMPA NZ, .Ldtc_sirius32_is_pending_logic_true_4
    JMPA UC, .Ldtc_sirius32_is_pending_logic_false_5
.Ldtc_sirius32_is_pending_logic_true_4:
	MOV      R0, #1              
    JMPA UC, .Ldtc_sirius32_is_pending_logic_end_6
.Ldtc_sirius32_is_pending_logic_false_5:
	MOV      R0, #0              
.Ldtc_sirius32_is_pending_logic_end_6:
	; source: /tmp/dtc_fw_module.c:502
	CMP      R0, #0              
    JMPA NZ, .Ldtc_sirius32_is_pending_if_then_0
    JMPA UC, .Ldtc_sirius32_is_pending_if_end_2
.Ldtc_sirius32_is_pending_if_then_0:
	; source: /tmp/dtc_fw_module.c:501
	MOV      R0, #0              
	ADD      SP, #6              ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET                          
.Ldtc_sirius32_is_pending_if_end_2:
	; source: /tmp/dtc_fw_module.c:502
	MOV      R0, [R15+#0]        ; R0 = st
	MOV      R1, [R15+#4]        ; R1 = e
	MOV      R2, #6              
	MOV      R3, R1              
	ADD      R3, R2              
	MOVB     R1, [R3]            
	AND      R1, #0x00FF         
	MOV      R2, #2              
	MUL     R1, R2              
	MOV      R3, MDL             ; low word of MDL:MDH product
	MOV      R1, R0              
	ADD      R1, R3              
	MOV      R0, [R1]            
	MOV      R1, [R15+#4]        ; R1 = e
	MOV      R2, #2              
	MOV      R3, R1              
	ADD      R3, R2              
	MOV      R1, [R3]            
	MOV      R2, R0              
	AND      R2, R1              
	MOV      R0, #0              
	CMP      R2, R0              
    JMPA NZ, .Lcmp_true_19
	MOV      R1, #0              
    JMPA UC, .Lcmp_end_19
.Lcmp_true_19:
	MOV      R1, #1              
.Lcmp_end_19:
	MOV      R0, R1              ; return value
	ADD      SP, #6              ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET                          

dtc_sirius32_is_confirmed:
	PUSH     R15                 ; save caller's frame pointer
	SUB      SP, #6              ; allocate locals + spills
	MOV      R15, SP             ; establish frame pointer
	MOV      [R15+#0], R4        ; spill incoming parameter 'st'
	MOVB     [R15+#2], R5        ; spill incoming parameter 'index'
	; source: /tmp/dtc_fw_module.c:507
	MOVB     R0, [R15+#2]        ; R0 = index
	AND      R0, #0x00FF         
	MOV      R4, R0              
    CALLA UC, dtc_sirius32_get
	MOV      R1, R0              ; function result
	MOV      [R15+#4], R1        ; e = R1
	; source: /tmp/dtc_fw_module.c:508
	MOV      R0, [R15+#4]        ; R0 = e
	CMP      R0, #0              
    JMPA Z, .Lnot_true_8
	MOV      R1, #0              
    JMPA UC, .Lnot_end_8
.Lnot_true_8:
	MOV      R1, #1              
.Lnot_end_8:
	CMP      R1, #0              
    JMPA NZ, .Ldtc_sirius32_is_confirmed_logic_true_4
.Ldtc_sirius32_is_confirmed_or_rhs_3:
	MOV      R0, [R15+#4]        ; R0 = e
	MOV      R1, #4              
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R2]            
	CMP      R0, #0              
    JMPA Z, .Lnot_true_9
	MOV      R1, #0              
    JMPA UC, .Lnot_end_9
.Lnot_true_9:
	MOV      R1, #1              
.Lnot_end_9:
	CMP      R1, #0              
    JMPA NZ, .Ldtc_sirius32_is_confirmed_logic_true_4
    JMPA UC, .Ldtc_sirius32_is_confirmed_logic_false_5
.Ldtc_sirius32_is_confirmed_logic_true_4:
	MOV      R0, #1              
    JMPA UC, .Ldtc_sirius32_is_confirmed_logic_end_6
.Ldtc_sirius32_is_confirmed_logic_false_5:
	MOV      R0, #0              
.Ldtc_sirius32_is_confirmed_logic_end_6:
	; source: /tmp/dtc_fw_module.c:509
	CMP      R0, #0              
    JMPA NZ, .Ldtc_sirius32_is_confirmed_if_then_0
    JMPA UC, .Ldtc_sirius32_is_confirmed_if_end_2
.Ldtc_sirius32_is_confirmed_if_then_0:
	; source: /tmp/dtc_fw_module.c:508
	MOV      R0, #0              
	ADD      SP, #6              ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET                          
.Ldtc_sirius32_is_confirmed_if_end_2:
	; source: /tmp/dtc_fw_module.c:509
	MOV      R0, [R15+#0]        ; R0 = st
	MOV      R1, #10             
	MOV      R2, R0              
	ADD      R2, R1              
	MOV      R0, [R15+#4]        ; R0 = e
	MOV      R1, #6              
	MOV      R3, R0              
	ADD      R3, R1              
	MOVB     R0, [R3]            
	AND      R0, #0x00FF         
	MOV      R1, #2              
	MUL     R0, R1              
	MOV      R3, MDL             ; low word of MDL:MDH product
	MOV      R0, R2              
	ADD      R0, R3              
	MOV      R1, [R0]            
	MOV      R0, [R15+#4]        ; R0 = e
	MOV      R2, #2              
	MOV      R3, R0              
	ADD      R3, R2              
	MOV      R0, [R3]            
	MOV      R2, R1              
	AND      R2, R0              
	MOV      R0, #0              
	CMP      R2, R0              
    JMPA NZ, .Lcmp_true_20
	MOV      R1, #0              
    JMPA UC, .Lcmp_end_20
.Lcmp_true_20:
	MOV      R1, #1              
.Lcmp_end_20:
	MOV      R0, R1              ; return value
	ADD      SP, #6              ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET                          


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

; 10_crc16.asm - CRC16 (poly 0xA001, família IBM/ARC) genuinamente
; COMPILADO de reimplementacao_c/checksum/crc16_sirius32.c pelo c167cc deste
; repositório (compiler/), via o mesmo pipeline usado pra 08_dtc_table.asm
; (concatenar tabela+função -> c167cc --dump-asm -> port_real_abi.py). Não é
; hand-written.
;
; IMPORTANTE (honestidade de escopo, ver crc16_sirius32.h): este módulo é o
; núcleo de CÁLCULO (crc16_sirius32(buf,len,init) = crc = (crc>>8) ^
; tab[(crc^b)&0xFF], validado byte-a-byte contra ../../crc_sirius32.py e
; contra 2 dumps reais de 256KB - ver simulador/firmware_min/README.md).
; NÃO existe, até hoje, nenhum SID/código legado reconstruído em
; reimplementacao_c/kline/ que chame checksum de verdade via quadro K-line -
; então este módulo fica embutido no binário mas SEM handler de dispatcher
; que o invoque (nenhum trampolim DTC_*-like foi criado). Wire-lo a um SID
; seria inventar comportamento nunca reverse-engineered - não fazer isso até
; achar (ou confirmar a ausência d)o call site real.
;
; Convenção de chamada: ABI real do c167cc (R4=buf, R5=len, R6=init;
; resultado em R0) - não a convenção R0-entrada/R1-saída usada pelos outros
; fragmentos hand-written deste diretório. Uso (quando/se houver call site):
;     MOV R4, #ptr_buf
;     MOV R5, #tamanho
;     MOV R6, #valor_inicial
;     CALLA UC, crc16_sirius32
;     ; resultado em R0

CRC16_SIRIUS32_TABLE:		DW	0,49345,49537,320,49921,960,640,49729,50689,1728,1920,51009,1280,50625,50305,1088,52225,3264,3456,52545,3840,53185,52865,3648,2560,51905,52097,2880,51457,2496,2176,51265,55297,6336,6528,55617,6912,56257,55937,6720,7680,57025,57217,8000,56577,7616,7296,56385,5120,54465,54657,5440,55041,6080,5760,54849,53761,4800,4992,54081,4352,53697,53377,4160,61441,12480,12672,61761,13056,62401,62081,12864,13824,63169,63361,14144,62721,13760,13440,62529,15360,64705,64897,15680,65281,16320,16000,65089,64001,15040,15232,64321,14592,63937,63617,14400,10240,59585,59777,10560,60161,11200,10880,59969,60929,11968,12160,61249,11520,60865,60545,11328,58369,9408,9600,58689,9984,59329,59009,9792,8704,58049,58241,9024,57601,8640,8320,57409,40961,24768,24960,41281,25344,41921,41601,25152,26112,42689,42881,26432,42241,26048,25728,42049,27648,44225,44417,27968,44801,28608,28288,44609,43521,27328,27520,43841,26880,43457,43137,26688,30720,47297,47489,31040,47873,31680,31360,47681,48641,32448,32640,48961,32000,48577,48257,31808,46081,29888,30080,46401,30464,47041,46721,30272,29184,45761,45953,29504,45313,29120,28800,45121,20480,37057,37249,20800,37633,21440,21120,37441,38401,22208,22400,38721,21760,38337,38017,21568,39937,23744,23936,40257,24320,40897,40577,24128,23040,39617,39809,23360,39169,22976,22656,38977,34817,18624,18816,35137,19200,35777,35457,19008,19968,36545,36737,20288,36097,19904,19584,35905,17408,33985,34177,17728,34561,18368,18048,34369,33281,17088,17280,33601,16640,33217,32897,16448		; array, inicializado


crc16_sirius32:
	PUSH     R15                 ; save caller's frame pointer
	SUB      SP, #10             ; allocate locals + spills
	MOV      R15, SP             ; establish frame pointer
	MOV      [R15+#0], R4        ; spill incoming parameter 'buf'
	MOV      [R15+#2], R5        ; spill incoming parameter 'len'
	MOV      [R15+#4], R6        ; spill incoming parameter 'init'
	MOV      R0, [R15+#4]        ; R0 = init
	MOV      [R15+#6], R0        ; crc = R0
	MOV      R0, #0
	MOV      [R15+#8], R0        ; i = R0
.Lcrc16_sirius32_for_cond_0:
	MOV      R0, [R15+#8]        ; R0 = i
	MOV      R1, [R15+#2]        ; R1 = len
	CMP      R0, R1
    JMPA C, .Lcrc16_sirius32_cmp_true_1
	MOV      R2, #0
    JMPA UC, .Lcrc16_sirius32_cmp_end_1
.Lcrc16_sirius32_cmp_true_1:
	MOV      R2, #1
.Lcrc16_sirius32_cmp_end_1:
	CMP      R2, #0
    JMPA NZ, .Lcrc16_sirius32_for_body_1
    JMPA UC, .Lcrc16_sirius32_for_end_3
.Lcrc16_sirius32_for_body_1:
	MOV      R0, [R15+#6]        ; R0 = crc
	MOV      R1, #8
	MOV      R2, R0
	SHR      R2, R1
	MOV      R0, #CRC16_SIRIUS32_TABLE; near address of global
	MOV      R1, [R15+#6]        ; R1 = crc
	MOV      R3, [R15+#0]        ; R3 = buf
	MOV      R8, [R15+#8]        ; R8 = i
	MOV      R9, R3
	ADD      R9, R8
	MOVB     R3, [R9]
	AND      R3, #0x00FF
	MOV      R8, R1
	XOR      R8, R3
	MOV      R1, #255
	MOV      R3, R8
	AND      R3, R1
	MOV      R1, #2
	MUL     R3, R1
	MOV      R8, MDL             ; low word of MDL:MDH product
	MOV      R1, R0
	ADD      R1, R8
	MOV      R0, [R1]
	MOV      R1, R2
	XOR      R1, R0
	MOV      [R15+#6], R1        ; crc = R1
.Lcrc16_sirius32_for_post_2:
	MOV      R0, [R15+#8]        ; R0 = i
	MOV      R1, #1
	MOV      R2, R0
	ADD      R2, R1
	MOV      [R15+#8], R2        ; i = R2
    JMPA UC, .Lcrc16_sirius32_for_cond_0
.Lcrc16_sirius32_for_end_3:
	MOV      R0, [R15+#6]        ; R0 = crc
	ADD      SP, #10             ; release locals + spills
	POP      R15                 ; restore caller's frame pointer
	RET

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

