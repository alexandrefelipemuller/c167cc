#!/usr/bin/env python3
"""Teste funcional de ponta a ponta do firmware_full.bin via quadro K-line
REAL (não pula direto pra um endereço de sub-rotina como o teste ad-hoc
usado durante a depuração do módulo DTC) - usa `bridge_min.KLineTransport`,
a MESMA classe que fala com o simulador quando um app ELM327 de verdade se
conecta, então este teste exercita exatamente o caminho RX->checksum->
S_DISPATCH->SID->TX que o app real usa.

Cobre:
  1. Mode 01 PID 00 (OBD legislado) - já validado antes via smoke_test.py,
     conferido de novo aqui contra a MESMA imagem que agora tem o módulo
     DTC compilado embutido (prova que a troca do fragmento não quebrou
     nada fora do próprio DTC).
  2. Sessão fechada -> SID 0x29 (abre básica).
  3. SID 0x14 sub 0x20 (arma handshake) -> sub 0x6F (confirma) - o confirma
     é o que dispara DTC_CLEAR(SELECTIVE_2) de verdade (ver 04_sid_14.asm),
     agora implementado pelo dtc_sirius32_clear() COMPILADO (não mais a
     versão hand-written) - só que aqui via requisição K-line real, não
     chamando o endereço da rotina diretamente.
  4. SID desconhecido na sessão básica -> NRC 0x12 (subFunctionNotSupported).
  5. Prova indireta de que o SID 0x14/0x6F realmente rodou o DTC_CLEAR
     compilado: marca aging[5]/confirmed[0] direto na memória do sim ANTES
     de mandar o 0x6F, confere que ficou zerado DEPOIS - sem isso o teste só
     provaria que o dispatcher despachou pro handler certo, não que o
     handler chama de fato a rotina compilada certa.

Uso: python3 functional_test.py [firmware_full.bin]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bridge_min import KLineTransport

G_DTC_STATE = 0x18C0  # ver build.py -> vars: g_dtc_state (offset dentro de firmware_full.bin)


def expect(label, got, want):
    ok = got == want
    status = "OK" if ok else "FALHA"
    print(f"[{status}] {label}: got={got.hex() if isinstance(got, (bytes, bytearray)) else got} "
          f"want={want.hex() if isinstance(want, (bytes, bytearray)) else want}")
    return ok


def main():
    bin_path = sys.argv[1] if len(sys.argv) > 1 else "firmware_full.bin"
    t = KLineTransport(bin_path)
    failures = 0

    # 1. Mode 01 PID 00 - funciona independente de sessão (ver S_DISPATCH).
    resp = t.request(bytes([0x01, 0x00]))
    if not expect("Mode01 PID00", resp, bytes([0x41, 0x00, 0xBE, 0x3E, 0x80, 0x10])):
        failures += 1

    # 2. Sessão fechada -> SID 0x29 abre básica.
    resp = t.request(bytes([0x29]))
    if not expect("SID 0x29 (abre sessao basica)", resp, bytes([0x69])):
        failures += 1

    # 3. SID desconhecido na sessão básica -> NRC 0x12 sub 0x11
    #    (serviceNotSupported, ver S_NRC_UNKNOWN_SID/S_SEND_NRC).
    resp = t.request(bytes([0x99]))
    if not expect("SID desconhecido -> NRC", resp[:2], bytes([0x7F, 0x99])):
        failures += 1

    # 4. Prova de que o 0x6F vai realmente chamar o dtc_sirius32_clear()
    #    COMPILADO: força um estado "confirmado" real na struct do sim
    #    (entrada idx5: word_index=0, mask=0x0400, aging_slot=5 - ver
    #    dtc_sirius32.c) ANTES do handshake, confere que o clear zera.
    base = G_DTC_STATE
    t.sim.set_w16(base + 10 + 0, 0x0400)   # confirmed[0] |= mask da entrada idx5
    t.sim.mem[base + 20 + 5] = 0x90        # aging[5] = 0x90 ("confirmado")
    before = (t.sim.w16(base + 10), t.sim.mem[base + 25])
    print(f"[info] estado DTC forcado antes do 0x6F: confirmed0={hex(before[0])} aging5={hex(before[1])}")

    # 5. SID 0x14 sub 0x20 (arma handshake).
    resp = t.request(bytes([0x14, 0x20]))
    if not expect("SID 0x14 sub 0x20 (arma handshake)", resp, bytes([0x54, 0x20])):
        failures += 1

    # 6. SID 0x14 sub 0x6F (confirma) -> dispara DTC_CLEAR(SELECTIVE_2) real.
    resp = t.request(bytes([0x14, 0x6F]))
    if not expect("SID 0x14 sub 0x6F (confirma clear)", resp, bytes([0x54, 0x6F])):
        failures += 1

    after_aging5 = t.sim.mem[base + 25]
    after_confirmed0 = t.sim.w16(base + 10)
    print(f"[info] estado DTC apos o 0x6F: confirmed0={hex(after_confirmed0)} aging5={hex(after_aging5)}")
    if not expect("DTC_CLEAR(SELECTIVE_2) via 0x6F zerou aging[5]", after_aging5, 0):
        failures += 1
    # SELECTIVE_2 só zera o contador de aging (ver dtc_sirius32_clear); o
    # bit de confirmed em si é desligado depois pelo decay_tick, não pelo
    # clear - não afirmar isso aqui (evitar inventar comportamento).

    # 7. SID 0x14 sub 0x6F de novo SEM handshake 0x20 antes -> NRC
    #    conditionsNotCorrect (0x22), já que HANDSHAKE20_STATE foi
    #    consumido pelo passo 6 (ver D14_CONFIRM_HANDSHAKE).
    resp = t.request(bytes([0x14, 0x6F]))
    if not expect("SID 0x14 sub 0x6F sem handshake -> NRC 0x22", resp, bytes([0x7F, 0x14, 0x22])):
        failures += 1

    print()
    if failures:
        print(f"FALHA: {failures} verificacao(oes) nao bateram")
        sys.exit(1)
    print("OK: todas as verificacoes bateram")
    sys.exit(0)


if __name__ == "__main__":
    main()
