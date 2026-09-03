"""Ponte TCP <-> simulador C166 real, emulando um adaptador ELM327 - permite
plugar um scanner OBD2 de verdade (qualquer app que fale ELM327 por TCP/wifi,
ex. Torque, OBD Fusion, ScanMaster) contra o FIRMWARE REAL rodando dentro do
`c166sim.py`, não uma reimplementação (ver `../reimplementacao_c/`, que é
lógica reconstruída à mão - responder lá não provaria nada sobre o binário
real estar íntegro).

Uso:
    python3 obd2_bridge.py "../mapas/Clio RS1 GrN.ori" --port 35000

Depois conecta o app de scanner num adaptador WiFi/TCP ELM327 apontando pro
IP:porta desta máquina.

## Simplificações ACEITAS nesta rodada (pedido explícito do usuário, 20/08/2026)

1. **Sem pulso elétrico de fast-init** (25ms low + 25ms high antes do sync
   `0x55`) - pula direto pra injetar o sync a 10.400 baud, como se o wakeup
   já tivesse acontecido. Mais simples, mas não testa o estado 0/1 do
   handshake (`file 0x1e30`/`0x1e7c`) que decodifica esse pulso de verdade.
2. **Formato de quadro no fio é um PALPITE, não confirmado**: a pesquisa de
   RE do projeto (`DECODIFICACAO.md`, `reimplementacao_c/kline/README.md`)
   é explícita que o formato exato (header/tamanho/checksum) desse firmware
   **nunca foi decifrado** - o handler que remontaria isso (`file 0x3504`)
   não foi decompilado instrução a instrução. Aqui é usado o mesmo palpite
   já usado como placeholder no `reimplementacao_c` (ISO 14230-2 "padrão de
   indústria": 1 byte formato = 0x80|tamanho, payload, 1 byte checksum =
   soma 8-bit de tudo antes). Se o firmware não responder nada (ou
   responder lixo), o mais provável é o formato de quadro estar errado, não
   que o mapa esteja corrompido - ver `mapas/README.md`.
3. **Sem timing de baud rate real** - troca de byte é síncrona/instantânea
   (mesma simplificação já aceita no resto do simulador pra outros
   periféricos). Entre bytes injetados, dá tempo de CPU (`STEPS_PER_BYTE`)
   pro firmware processar cada um antes do próximo chegar (hardware real só
   entrega 1 byte por vez de qualquer forma - sem esse intervalo, o byte
   anterior seria sobrescrito em S0RBUF antes do firmware ler).

## Arquitetura (deixada pronta pra trocar a camada ELM327 por bytes crus)

`KLineTransport` = a parte que fala com o `Sim` de verdade (só sabe mandar
uma requisição crua e devolver os bytes de resposta crus, sem noção de AT
command). `ELM327Session` = por cima, traduz TCP <-> texto AT/hex ASCII
<-> chamadas em `KLineTransport`. Pra usar bytes crus no futuro (ligar um
app com "raw serial over TCP"): trocar só o que fica dentro de
`handle_line()` que decide AT vs hex de dados - `KLineTransport` não muda.
"""
import socket
import sys
import time

sys.path.insert(0, '.')
import c166sim


STEPS_PER_BYTE = 2_000        # teto de segurança pra `_wait_byte_consumed()` - o decodificador
                               # de cabeçalho real (`file 0x3474`) exige que os 3 bytes de
                               # header cheguem dentro de uma janela curta (compara contra o
                               # mesmo limiar ~0xC8=200 "ticks" já visto no handshake), mas o
                               # ATRASO da nossa ISR sintética até disparar é variável (guarda
                               # de não-reentrância com o CC26) - por isso não esperamos um
                               # número fixo de passos, esperamos a flag S0RIR baixar de verdade
                               # (ver `_wait_byte_consumed`); este valor é só o teto de segurança
STEPS_WAIT_RESPONSE = 400_000  # orçamento total de steps esperando resposta
IDLE_STEPS_PER_TICK = 20_000  # steps rodados enquanto não há nada pra fazer


# HACK pragmático (20/08/2026, pedido explícito) - pula a máquina de estados
# de wakeup fast-init (`file 0x39A`, estados 0-14, decompilada nesta sessão)
# em vez de tentar bit-bangear o pulso elétrico 25ms low/25ms high no pino
# `0xFFC4.7` com timing preciso (não vale o esforço pra um protótipo). O
# estado 15 (`file 0x23B6`) é o estado "conectado, esperando tráfego" - só
# fica testando bits que a ISR real do ASC0 (`file 0x3406`, já ativa e
# funcional no simulador) seta quando bytes chegam de verdade. Forçar
# `0xFC5D=0x0F` direto na RAM pula só a NEGOCIAÇÃO (sync/key bytes), não o
# recebimento de mensagem em si - hipótese não confirmada por captura real,
# só por leitura do código; se não funcionar, o próximo passo seria simular
# o pulso elétrico de verdade via mock do registrador de porta `0xFFC4`.
KLINE_HANDSHAKE_STATE_ADDR = 0xFC5D
KLINE_STATE_CONNECTED = 0x0F
# `0xFC66`/`0xFC67` = estado da camada física de recepção (tabela `file
# 0x3BA`) - o estado 15 do handshake acima NÃO seta isso sozinho, quem faz é
# o estado 8 (`file 0x2168`, pulado pelo hack acima). Sem replicar esse
# efeito colateral, o decodificador de mensagem fica parado pra sempre no
# estado 0 (só grava timestamp, nunca lê S0RBUF) - achado 20/08/2026
# testando a injeção direto.
KLINE_PHY_STATE_ADDR = 0xFC66
KLINE_PHY_STATE_LISTENING = 0x04
KLINE_PHY_SUBSTATE_ADDR = 0xFC67
KLINE_PHY_SUBSTATE_VALUE = 0x01

# Formato de quadro real (decompilado 20/08/2026 em `file 0x3474`/`0x3504`,
# tabela `file 0x3BA` estado 4/5) - NÃO é mais o palpite ISO14230-2 genérico
# antigo. Cabeçalho endereçado: [FMT][TGT=0x33][SRC=0xF1] + payload +
# checksum (soma 8-bit de tudo). `TGT=0x33` reusa o mesmo byte de sync do
# handshake fast-init; `SRC=0xF1` é o endereço padrão de ferramenta de
# diagnóstico (bate com o que um app OBD2 de verdade manda). O nibble alto
# do FMT (`0xC0` vs `0x80`) é escolhido por um bit de calibração (`0xFDFE.2`,
# mesmo bit que escolhe a variante de key byte no handshake) - usamos
# `0xC0` (endereçamento físico) como palpite inicial; se o firmware real não
# responder, o próximo passo é tentar `0x80`.
KLINE_TARGET_ADDR = 0x7A  # corrigido 20/08/2026: a checagem de aceite final (file 0x3564,
                          # só alcançada com FDFE.2==0) exige TGT==0x7A (ou 0xFF broadcast,
                          # gated por FDFE.1) - 0x33 só é válido no ramo FDFE.2==1 (não é o
                          # caso deste boot); 0x33 é reusado como sync do handshake, não como
                          # endereço de quadro nesta variante
KLINE_SOURCE_ADDR = 0xF1
KLINE_FMT_HIGH_NIBBLE = 0x80  # confirmado 20/08/2026: 0xFDFE.2==0 neste boot -> variante 0x80

# Watchdog calibrado (20/08/2026, `c166sim.py` WDT_TICK_NUMERATOR/DENOMINATOR)
# - ligado por padrão nesta ponte porque o boot real deste firmware passa
# por resets de watchdog como parte normal da sequência (self-loop de trap
# + march test do POST) - sem isso o boot trava de vez no self-loop e nunca
# chega no superloop de verdade. `MAX_RESET_RETRIES` limita quantas vezes
# reenviamos o quadro se um reset acontecer no meio da espera de resposta.
MAX_RESET_RETRIES = 6


class KLineTransport:
    """Fala com o `Sim` de verdade via ASC0 (ver UART_* em c166sim.py) - só
    conhece bytes crus de request/response, nenhuma noção de ELM327/AT."""

    def __init__(self, image_path, skip_handshake=True, wdt_enabled=True):
        with open(image_path, 'rb') as f:
            image = f.read()
        self.sim = c166sim.Sim(image)
        self.image_path = image_path
        self.skip_handshake = skip_handshake
        self.sim._wdt_enabled = wdt_enabled

    def idle_tick(self, steps=IDLE_STEPS_PER_TICK):
        """Deixa o firmware rodar sozinho (superloop, watchdog, ADC, CRC de
        fundo etc.) enquanto não há requisição pendente - sem isso, tarefas
        de fundo do firmware (ex. a task de CRC contínua já documentada no
        README do simulador) param de avançar."""
        self._run_catching_trap(steps)

    def _run_catching_trap(self, steps):
        try:
            for _ in range(steps):
                self.sim.step()
        except c166sim.Trap as e:
            print(f"[sim] Trap durante execução: {e} (pc=0x{self.sim.pc:06X})", file=sys.stderr)

    def _wait_byte_consumed(self, max_steps=STEPS_PER_BYTE):
        """Roda até a ISR sintética (`_check_asc0_rx_isr`, `c166sim.py`)
        terminar de processar o byte injetado - espera o `RETI` de verdade
        (`self.sim._hw_irq_sp_watermark` voltar a `None`), NÃO só a flag
        `S0RIR` baixar. Achado 20/08/2026 rastreando byte a byte: a flag é
        limpa logo na 2ª instrução da ISR (`BCLR 0xFF6E.7`), bem ANTES dela
        ler `S0RBUF` de fato (~15 instruções depois) - esperar só a flag
        injetava o PRÓXIMO byte no meio da execução da ISR anterior,
        sobrescrevendo `S0RBUF` antes dela ser lida (mesma classe de bug do
        `STEPS_PER_BYTE` fixo antigo, só que na outra ponta)."""
        saw_pending = False
        for _ in range(max_steps):
            self.sim.step()
            ric_set = self.sim.w16(c166sim.Sim.UART_RIC_ADDR) & 0x80
            watermark_busy = self.sim._hw_irq_sp_watermark is not None
            if ric_set or watermark_busy:
                saw_pending = True
            elif saw_pending:
                return  # RIC já baixou E nenhuma IRQ (CC26/ASC0) em voo - ISR realmente terminou
        print("[sim] AVISO: byte K-line não foi consumido a tempo (RIC ainda setado)", file=sys.stderr)

    @staticmethod
    def _frame(payload, fmt_high_nibble=KLINE_FMT_HIGH_NIBBLE):
        """Cabeçalho endereçado real - ver comentário grande acima de
        `KLINE_TARGET_ADDR`."""
        length = len(payload)
        if length > 63:
            raise ValueError("payload > 63 bytes não cabe no formato curto (não implementado)")
        fmt = fmt_high_nibble | length
        body = bytes([fmt, KLINE_TARGET_ADDR, KLINE_SOURCE_ADDR]) + bytes(payload)
        checksum = sum(body) & 0xFF
        return body + bytes([checksum])

    def _seed_handshake_skip(self):
        # reaplica a cada tentativa - o boot zera a RAM antes de chegar no
        # superloop, então um poke só em __init__/1x seria sobrescrito
        self.sim.mem[KLINE_HANDSHAKE_STATE_ADDR] = KLINE_STATE_CONNECTED
        self.sim.mem[KLINE_PHY_STATE_ADDR] = KLINE_PHY_STATE_LISTENING
        self.sim.mem[KLINE_PHY_SUBSTATE_ADDR] = KLINE_PHY_SUBSTATE_VALUE

    def _send_frame(self, frame):
        for b in frame:
            self.sim.uart_inject_rx_byte(b)
            self._wait_byte_consumed()

    def request(self, payload_bytes, max_retries=MAX_RESET_RETRIES):
        """Manda 1 requisição K-line (SID + dados, sem frame) pro firmware
        real e devolve os bytes crus de resposta que ele transmitiu de
        volta (também sem tirar o frame - quem chama decide o que fazer).

        Achado 20/08/2026: o boot real deste firmware passa por uma cadeia
        de MÚLTIPLOS resets de watchdog antes de estabilizar (o self-loop
        de trap já documentado + o teste de march de RAM do POST, que
        também esbarra no watchdog) - e cada reset LIMPA a flag de
        "mensagem pronta" (`0xFD06.13`) antes do escalonador ter chance de
        consumir ela, mesmo com a RAM preservada. Um scanner OBD2 de
        verdade lida com isso retentando a requisição até obter resposta -
        reproduzimos o mesmo comportamento aqui: se um reset acontecer
        enquanto esperamos, reenviamos o quadro."""
        if self.skip_handshake:
            self._seed_handshake_skip()
        frame = self._frame(payload_bytes)
        self._send_frame(frame)

        collected = []
        budget = STEPS_WAIT_RESPONSE
        chunk = 2_000
        idle_ticks_without_data = 0
        retries_left = max_retries
        last_reset_count = self.sim.wdt_reset_count
        while budget > 0:
            self._run_catching_trap(min(chunk, budget))
            budget -= chunk
            if self.sim.wdt_reset_count != last_reset_count:
                last_reset_count = self.sim.wdt_reset_count
                if retries_left <= 0 or collected:
                    break
                retries_left -= 1
                if self.skip_handshake:
                    self._seed_handshake_skip()
                self._send_frame(frame)
                budget = STEPS_WAIT_RESPONSE  # reset do orçamento pra essa nova tentativa
                idle_ticks_without_data = 0
                continue
            got = self.sim.uart_pop_tx_bytes()
            if got:
                collected.extend(got)
                idle_ticks_without_data = 0
            elif collected:
                idle_ticks_without_data += 1
                if idle_ticks_without_data > 20:  # ~40k steps sem byte novo = resposta acabou
                    break
        return bytes(collected)


class ELM327Session:
    """Emulador ELM327 mínimo - só o suficiente pra apps de scanner comuns
    completarem o handshake inicial (ATZ/ATE0/ATL0/ATS0/ATH.../ATSP...) e
    mandarem requisições hex. Qualquer AT command não reconhecido responde
    'OK' (aceita e ignora) em vez de erro, pra não travar o app - ver
    `handle_line()`."""

    PROMPT = b"\r>"

    def __init__(self, transport):
        self.t = transport
        self.echo = True
        self.headers = False

    def handle_line(self, line):
        line = line.strip()
        if not line:
            return b""
        upper = line.upper()

        if upper.startswith("AT"):
            return self._handle_at(upper)

        # linha de dados: hex ASCII (ex. "0100", "01 00", "220105")
        hexdigits = "".join(ch for ch in line if ch in "0123456789abcdefABCDEF")
        if len(hexdigits) % 2 != 0:
            return b"?" + self.PROMPT
        try:
            payload = bytes.fromhex(hexdigits)
        except ValueError:
            return b"?" + self.PROMPT
        if not payload:
            return b"?" + self.PROMPT

        print(f"[elm327] request: {payload.hex()}")
        response = self.t.request(payload)
        print(f"[elm327] response crua: {response.hex() if response else '(nada)'}")
        if not response:
            return b"NO DATA" + self.PROMPT
        hex_out = response.hex().upper()
        spaced = " ".join(hex_out[i:i + 2] for i in range(0, len(hex_out), 2))
        return spaced.encode() + self.PROMPT

    def _handle_at(self, upper):
        if upper == "ATZ":
            return b"ELM327 v1.5" + self.PROMPT
        if upper in ("ATE0", "ATE1"):
            self.echo = upper == "ATE1"
            return b"OK" + self.PROMPT
        if upper in ("ATH0", "ATH1"):
            self.headers = upper == "ATH1"
            return b"OK" + self.PROMPT
        if upper == "ATI":
            return b"ELM327 v1.5" + self.PROMPT
        if upper == "ATRV":
            return b"12.3V" + self.PROMPT  # não modelado - valor fixo
        if upper == "ATDP":
            return b"ISO 14230-4 KWP (fast init)" + self.PROMPT
        # ATL0/ATS0/ATSPx/ATAT.../ATST.../etc - aceita tudo, sem efeito modelado
        return b"OK" + self.PROMPT


def serve(image_path, port, skip_handshake=True):
    transport = KLineTransport(image_path, skip_handshake=skip_handshake)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(1)
    print(f"[bridge] simulando '{image_path}' na porta {port} (ELM327 sobre TCP)")
    print(f"[bridge] pc inicial=0x{transport.sim.pc:06X}")

    while True:
        print("[bridge] esperando conexão do scanner...")
        conn, addr = srv.accept()
        print(f"[bridge] conectado: {addr}")
        conn.settimeout(0.05)
        session = ELM327Session(transport)
        buf = b""
        try:
            while True:
                try:
                    data = conn.recv(4096)
                    if not data:
                        break
                    buf += data
                    while b"\r" in buf:
                        line, buf = buf.split(b"\r", 1)
                        reply = session.handle_line(line.decode(errors="replace"))
                        if reply:
                            conn.sendall(reply)
                except socket.timeout:
                    transport.idle_tick()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            conn.close()
            print("[bridge] desconectado")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bin_path")
    ap.add_argument("--port", type=int, default=35000)
    ap.add_argument("--no-skip-handshake", dest="skip_handshake", action="store_false",
                     help="não pular a negociação fast-init (roda o wakeup real, que hoje trava no estado 0)")
    args = ap.parse_args()
    serve(args.bin_path, args.port, skip_handshake=args.skip_handshake)
