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
