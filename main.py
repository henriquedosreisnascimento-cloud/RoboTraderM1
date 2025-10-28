# main.py
# ROBÔ TRADER M1 (VISUAL) - DASHBOARD NO NAVEGADOR
# Mostra sinais, resultados e assertividade em tempo real
# Funciona em BTC-USDT e ETH-USDT

import requests
import time
from datetime import datetime
from flask import Flask, render_template_string

# --- CONFIGURAÇÕES ---
ATIVOS = ["BTC-USDT", "ETH-USDT"]
PERCENT_DIFF = 0.02
HISTORICO = []
ULTIMO_SINAL = {"ativo": None, "tipo": None, "hora": None, "resultado": None}

# --- FLASK ---
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Robô M1 Dashboard</title>
    <style>
        body { font-family: Arial, sans-serif; background: #1e1e1e; color: #fff; }
        h1 { color: #00ffff; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 8px 12px; text-align: center; }
        th { background: #333; }
        tr:nth-child(even) { background: #2a2a2a; }
        .WIN { color: #00ff00; font-weight: bold; }
        .LOSS { color: #ff4d4d; font-weight: bold; }
        .sinal { font-weight: bold; }
    </style>
    <meta http-equiv="refresh" content="2">
</head>
<body>
    <h1>Robô M1 Dashboard</h1>
    <p><strong>Último Sinal:</strong> {{ ultimo_sinal.tipo }} | Ativo: {{ ultimo_sinal.ativo }} | Hora: {{ ultimo_sinal.hora }} | Resultado: {{ ultimo_sinal.resultado }}</p>
    <p><strong>Assertividade Atual:</strong> {{ assertividade }}% | Total Trades: {{ total_trades }}</p>
    <table border="1">
        <tr>
            <th>Hora</th>
            <th>Ativo</th>
            <th>Sinal</th>
            <th>Resultado</th>
        </tr>
        {% for h in historico %}
        <tr>
            <td>{{ h.hora }}</td>
            <td>{{ h.ativo }}</td>
            <td class="sinal">{{ h.sinal }}</td>
            <td class="{{ h.resultado }}">{{ h.resultado }}</td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""

# --- FUNÇÕES DE TRADING ---
def pegar_preco(ativo):
    url = f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={ativo}"
    try:
        r = requests.get(url, timeout=5)
        data = r.json()
        return float(data['data']['price'])
    except:
        return None

def calcular_momentum(precos):
    if len(precos) < 3:
        return 0
    return precos[-1] - precos[-3]

def pressao_vela(preco_abertura, preco_fechamento):
    return "alta" if preco_fechamento > preco_abertura else "baixa"

def gerar_sinal(preco_atual, preco_anterior, momentum, pressao):
    sinal = None
    if momentum > 0 and pressao == "alta":
        if (preco_atual - preco_anterior)/preco_anterior >= PERCENT_DIFF/100:
            sinal = "CALL"
    elif momentum < 0 and pressao == "baixa":
        if (preco_anterior - preco_atual)/preco_anterior >= PERCENT_DIFF/100:
            sinal = "PUT"
    return sinal

def checar_resultado(preco_entrada, preco_fechamento, tipo):
    if tipo == "CALL":
        return "WIN" if preco_fechamento > preco_entrada else "LOSS"
    elif tipo == "PUT":
        return "WIN" if preco_fechamento < preco_entrada else "LOSS"
    return None

# --- LOOP PRINCIPAL EM THREAD ---
import threading

def loop_robo():
    precos_anteriores = {at: [] for at in ATIVOS}
    precos_abertura = {at: None for at in ATIVOS}

    while True:
        for ativo in ATIVOS:
            preco_atual = pegar_preco(ativo)
            if preco_atual is None:
                continue

            if precos_abertura[ativo] is None:
                precos_abertura[ativo] = preco_atual

            precos_anteriores[ativo].append(preco_atual)
            if len(precos_anteriores[ativo]) > 10:
                precos_anteriores[ativo].pop(0)

            momentum = calcular_momentum(precos_anteriores[ativo])
            pressao = pressao_vela(precos_abertura[ativo], preco_atual)
            preco_anterior = precos_anteriores[ativo][-2] if len(precos_anteriores[ativo])>1 else preco_atual

            sinal = gerar_sinal(preco_atual, preco_anterior, momentum, pressao)
            if sinal:
                hora_sinal = datetime.now().strftime("%H:%M:%S")
                time.sleep(50)  # espera quase o final da vela
                preco_fechamento = pegar_preco(ativo)
                resultado = checar_resultado(preco_atual, preco_fechamento, sinal)

                HISTORICO.append({
                    "ativo": ativo,
                    "sinal": sinal,
                    "hora": hora_sinal,
                    "resultado": resultado
                })
                ULTIMO_SINAL.update({
                    "ativo": ativo,
                    "tipo": sinal,
                    "hora": hora_sinal,
                    "resultado": resultado
                })

            if datetime.now().second >= 55:
                precos_abertura[ativo] = None

        time.sleep(1)

# --- FLASK ROUTE ---
@app.route("/")
def dashboard():
    total_trades = len(HISTORICO)
    wins = sum(1 for h in HISTORICO if h["resultado"] == "WIN")
    assertividade = round((wins / total_trades)*100,1) if total_trades>0 else 0
    return render_template_string(HTML_TEMPLATE,
                                  historico=HISTORICO[::-1],
                                  ultimo_sinal=ULTIMO_SINAL,
                                  assertividade=assertividade,
                                  total_trades=total_trades)

# --- START THREAD ROBÔ E FLASK ---
if __name__ == "__main__":
    t = threading.Thread(target=loop_robo)
    t.start()
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
