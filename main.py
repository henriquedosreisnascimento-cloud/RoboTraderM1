# main.py
# ROBÔ TRADER M1 (OTIMIZADO) - PRESSÃO DE VELAS + MOMENTUM
# Funciona em BTC-USDT e ETH-USDT
# Avaliação de sinais a cada M1, com histórico e assertividade
# Preparado para rodar no Replit/GitHub

import requests
import json
import time
from datetime import datetime

# Configurações iniciais
ATIVOS = ["BTC-USDT", "ETH-USDT"]
PERCENT_DIFF = 0.02  # 2% de diferença mínima para sinal (ajustável)
HISTORICO = []
ULTIMO_SINAL = {"ativo": None, "tipo": None, "hora": None, "resultado": None}

# Função para pegar preço atual via API pública KuCoin
def pegar_preco(at):
    url = f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={at}"
    try:
        r = requests.get(url, timeout=5)
        data = r.json()
        return float(data['data']['price'])
    except:
        return None

# Função para calcular momentum simples
def calcular_momentum(precos):
    if len(precos) < 3:
        return 0
    return precos[-1] - precos[-3]

# Função para verificar pressão de vela (alta ou baixa)
def pressao_vela(preco_abertura, preco_fechamento):
    return "alta" if preco_fechamento > preco_abertura else "baixa"

# Função para gerar sinal
def gerar_sinal(preco_atual, preco_anterior, momentum, pressao):
    sinal = None
    # Filtro por momentum e pressão da vela
    if momentum > 0 and pressao == "alta":
        if (preco_atual - preco_anterior)/preco_anterior >= PERCENT_DIFF/100:
            sinal = "CALL"
    elif momentum < 0 and pressao == "baixa":
        if (preco_anterior - preco_atual)/preco_anterior >= PERCENT_DIFF/100:
            sinal = "PUT"
    return sinal

# Função para checar resultado do sinal
def checar_resultado(preco_entrada, preco_saida, tipo):
    if tipo == "CALL":
        return "WIN" if preco_saida > preco_entrada else "LOSS"
    elif tipo == "PUT":
        return "WIN" if preco_saida < preco_entrada else "LOSS"
    return None

# Função principal
def main():
    precos_anteriores = {at: [] for at in ATIVOS}
    precos_abertura = {at: None for at in ATIVOS}

    while True:
        for ativo in ATIVOS:
            preco_atual = pegar_preco(ativo)
            if preco_atual is None:
                continue

            # Guardar preço de abertura da vela
            if precos_abertura[ativo] is None:
                precos_abertura[ativo] = preco_atual

            # Histórico de preços para momentum
            precos_anteriores[ativo].append(preco_atual)
            if len(precos_anteriores[ativo]) > 10:
                precos_anteriores[ativo].pop(0)

            momentum = calcular_momentum(precos_anteriores[ativo])
            pressao = pressao_vela(precos_abertura[ativo], preco_atual)
            preco_anterior = precos_anteriores[ativo][-2] if len(precos_anteriores[ativo]) > 1 else preco_atual

            # Gerar sinal
            sinal = gerar_sinal(preco_atual, preco_anterior, momentum, pressao)
            if sinal:
                hora_sinal = datetime.now().strftime("%H:%M:%S")
                print(f"[{hora_sinal}] {ativo} -> SINAL: {sinal}")
                
                # Esperar até final da vela para avaliar resultado (50s)
                time.sleep(50)
                preco_fechamento = pegar_preco(ativo)
                resultado = checar_resultado(preco_atual, preco_fechamento, sinal)
                print(f"[{hora_sinal}] {ativo} -> RESULTADO: {resultado}")

                # Atualizar histórico
                HISTORICO.append({
                    "ativo": ativo,
                    "sinal": sinal,
                    "hora": hora_sinal,
                    "resultado": resultado
                })
                # Atualizar último sinal
                ULTIMO_SINAL.update({
                    "ativo": ativo,
                    "tipo": sinal,
                    "hora": hora_sinal,
                    "resultado": resultado
                })

            # Resetar preço de abertura no próximo minuto
            if datetime.now().second >= 55:
                precos_abertura[ativo] = None

        # Calcular assertividade
        if len(HISTORICO) > 0:
            wins = sum(1 for h in HISTORICO if h["resultado"] == "WIN")
            assertividade = round((wins / len(HISTORICO)) * 100, 1)
            print(f"Assertividade atual: {assertividade}% | Total trades: {len(HISTORICO)}")

        time.sleep(1)

if __name__ == "__main__":
    main()
