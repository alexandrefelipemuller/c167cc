"""Ponte TCP <-> simulador C166, para o firmware_min (ver src/*.asm,
montado por build.py em firmware_full.bin) - igual em espírito a
`../obd2_bridge.py`, mas para a NOSSA imagem própria. Diferenças
deliberadas em relação a `obd2_bridge.py`:

1. Sem `_seed_handshake_skip()`: não existe estado de handshake do firmware
   real pra "cutucar" via RAM - nosso firmware já começa pronto,
   esperando bytes em polling desde a primeira instrução.
2. Sem espera baseada em ISR (`_wait_byte_consumed()` real usa
   `_hw_irq_sp_watermark`, que só existe pra ISRs síntéticas amarradas a
   endereços do firmware real) - aqui rodamos um orçamento fixo de passos
   por byte (ver comentário em `_wait_byte_consumed`).
3. Desmancha o quadro K-line (remove [FMT][TGT][SRC] na frente e o byte de
   checksum no fim) antes de devolver pro `ELM327Session` - `obd2_bridge.py`
   original devolve os bytes crus do quadro sem tirar o cabeçalho, o que
   um app ELM327 de verdade (em modo padrão "headers off") não esperaria
   ver. Aqui expomos só o payload OBD2/KWP (ex. "41 00 BE 3E 80 10"), que é
   o que um adaptador ELM327 real mostraria por padrão.
4. SEM reset de `RX_COUNT` pelo lado Python: esse hack (usado no Stage 1,
   quando o codec de quadro ainda era de 6 bytes fixos) deixou de ser
   necessário a partir do Stage 1 de verdade - `01_frame_codec.asm` decodifica
   o campo de comprimento do FMT dinamicamente, então cada quadro se
   autodelimita e resincroniza sozinho mesmo depois de um quadro de
   tamanho diferente (testado explicitamente com uma sequência de comandos
   KWP de tamanho variável seguida de "0100" - ver checkpoint do plano).

Uso:
    python3 bridge_min.py firmware_full.bin --port 35000

Depois conecta um app ELM327 (Torque, OBD Fusion, ScanMaster, ou um cliente
de teste simples) no IP:porta desta máquina.
"""
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import c166sim

STEPS_PER_BYTE = 2_000
STEPS_WAIT_RESPONSE = 200_000
IDLE_STEPS_PER_TICK = 20_000

KLINE_TARGET_ADDR = 0x7A   # nosso próprio endereço
KLINE_SOURCE_ADDR = 0xF1   # endereço padrão de ferramenta de diagnóstico
KLINE_FMT_HIGH_NIBBLE = 0x80


class KLineTransport:
    """Fala com o `Sim` via ASC0 - só bytes crus de requisição/resposta, já
    com o quadro K-line removido/montado (payload OBD2 puro pra quem chama)."""

    def __init__(self, image_path):
        with open(image_path, 'rb') as f:
            image = f.read()
        self.sim = c166sim.Sim(image)
        self.image_path = image_path
        # Achado 21/08/2026: sem isso, o 1º byte injetado chega enquanto a
        # CPU ainda está rodando o boot (DTC_INIT, ~60 iterações) e nunca
        # sequer olha pra S0RIC a tempo - o gatilho síntetico de ISR do
        # simulador (pensado pro firmware real, ver `_wait_byte_consumed`)
        # sequestra o PC pra um endereço que não existe nesta imagem antes
        # do firmware chegar no loop de polling. Deixa o boot terminar e
        # estabilizar no polling antes de injetar qualquer coisa (mesmo
        # padrão já usado em `smoke_test.py`).
        self._run(50_000)

    def idle_tick(self, steps=IDLE_STEPS_PER_TICK):
        self._run(steps)

    def _run(self, steps):
        try:
            for _ in range(steps):
                self.sim.step()
        except c166sim.Trap as e:
            print(f"[sim] Trap durante execução: {e} (pc=0x{self.sim.pc:06X})", file=sys.stderr)

    def _wait_byte_consumed(self, steps=STEPS_PER_BYTE):
        """Roda um orçamento FIXO de passos por byte (não retorna assim que
        `S0RIC.S0RIR` baixa). Achado 21/08/2026: `c166sim.py` tem um gatilho
        síntetico incondicional (`_check_asc0_rx_isr`, pensado pro firmware
        REAL) que sequestra o PC pro endereço fixo da ISR real
        (`ASC0_RX_ISR_TARGET=0x103406`) sempre que `S0RIR` fica setado por
        `ASC0_RX_ISR_DELAY` (20) ciclos seguidos - endereço que não existe
        na NOSSA imagem. Se o próximo byte for injetado assim que `S0RIR`
        baixar (sem deixar nosso próprio `boot.asm` voltar de vez pro topo
        do `POLL`), o próximo `S0RIR` fica setado tempo demais enquanto
        ainda executamos o "rabo" do handler anterior (dispatch/store) e o
        sequestro dispara antes da gente conseguir limpar a flag de novo -
        PC vai pra 0x103406 (memória vazia) e nunca mais volta. Rodar um
        orçamento fixo generoso (em vez de sair assim que a flag baixar)
        garante que o programa já esteja de volta esperando no topo do
        `POLL` (looping sem fazer nada) bem antes do próximo byte chegar -
        mesma abordagem já validada em `smoke_test.py`."""
        for _ in range(steps):
            self.sim.step()

    @staticmethod
    def _frame(payload):
        length = len(payload)
        if length > 63:
            raise ValueError("payload > 63 bytes não cabe no formato curto")
        fmt = KLINE_FMT_HIGH_NIBBLE | length
        body = bytes([fmt, KLINE_TARGET_ADDR, KLINE_SOURCE_ADDR]) + bytes(payload)
        checksum = sum(body) & 0xFF
        return body + bytes([checksum])

    @staticmethod
    def _unframe(raw):
        """Tira [FMT][TGT][SRC] da frente e o checksum do fim, devolvendo só
        o payload OBD2. Se o quadro vier curto/mal formado (ex. nada
        respondido), devolve bytes vazios em vez de estourar - quem chama já
        trata "sem dados" como resposta válida (NO DATA)."""
        if len(raw) < 4:
            return b""
        return raw[3:-1]

    def _send_frame(self, frame):
        for b in frame:
            self.sim.uart_inject_rx_byte(b)
            self._wait_byte_consumed()

    def request(self, payload_bytes):
        """Manda 1 requisição OBD2 (SID+dados, sem quadro K-line) e devolve
        só o payload OBD2 de resposta (já sem cabeçalho/checksum)."""
        frame = self._frame(payload_bytes)
        self._send_frame(frame)

        collected = []
        budget = STEPS_WAIT_RESPONSE
        chunk = 2_000
        idle_ticks_without_data = 0
        while budget > 0:
            self._run(min(chunk, budget))
            budget -= chunk
            got = self.sim.uart_pop_tx_bytes()
            if got:
                collected.extend(got)
                idle_ticks_without_data = 0
            elif collected:
                idle_ticks_without_data += 1
                if idle_ticks_without_data > 20:
                    break
        return self._unframe(bytes(collected))


class ELM327Session:
    """Emulador ELM327 mínimo - idêntico em comportamento ao de
    `../obd2_bridge.py` (aceita handshake AT comum, responde 'OK' pra
    qualquer AT não reconhecido, traduz linhas hex <-> KLineTransport)."""

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
        print(f"[elm327] response: {response.hex() if response else '(nada)'}")
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
            return b"12.3V" + self.PROMPT
        if upper == "ATDP":
            return b"ISO 14230-4 KWP (fast init)" + self.PROMPT
        return b"OK" + self.PROMPT


def serve(image_path, port):
    transport = KLineTransport(image_path)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(1)
    print(f"[bridge] simulando firmware_min '{image_path}' na porta {port} (ELM327 sobre TCP)")
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
    args = ap.parse_args()
    serve(args.bin_path, args.port)
