# main.py
# ROBÔ TRADER M1 (WEB) - VERSÃO ASSERTIVIDADE POR CONFLUÊNCIA (3 REGRAS)
# 100% compatível com GitHub / Render / Railway / Replit
# Flask + Thread paralela + Atualização em tempo real (SSE)

from flask import Flask, Response
import requests
import time
from datetime import datetime, timedelta
import pytz
from threading import Thread, Lock
import os
import copy
import traceback
import json

# ====================== CONFIGURAÇÕES ======================
TIMEZONE_BR = 'America/Sao_Paulo'
ATIVOS_MONITORADOS = ['BTC-USDT', 'ETH-USDT', 'EUR-USDT']
API_BASE_URL = 'https://api.kucoin.com/api/v1/market/candles'
INTERVALO_M1 = '1min'
NUM_VELAS_ANALISE_M1 = 30
ASSERTIVIDADE_MINIMA = 100.0  # Requer 100% de confluência (3/3 regras)
MAX_HISTORICO = 10
PERCENTUAL_SL_TP = 0.0005
PERIOD_BB = 14
STD_DEV_BB = 2
PERIOD_RSI = 14
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
DASHBOARD_REFRESH_RATE_SECONDS = 5

# ====================== FLASK E ESTADO ======================
app = Flask(__name__)
state_lock = Lock()

def get_horario_brasilia():
    fuso = pytz.timezone(TIMEZONE_BR)
    return datetime.now(fuso)

ULTIMO_SINAL = {'horario': 'N/A', 'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0}
ULTIMO_SINAL_REGISTRADO = {'horario': 'N/A', 'sinal_tipo': 'N/A'}
HISTORICO_SINAIS = []
ULTIMO_SINAL_CHECAR = None

# ====================== INDICADORES ======================
def calculate_rsi(velas, period=PERIOD_RSI):
    if len(velas) < period:
        return 50.0
    closes = [v[1] for v in velas]
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [c for c in changes if c > 0]
    losses = [abs(c) for c in changes if c < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_bollinger_bands(velas, period=PERIOD_BB, std_dev=STD_DEV_BB):
    if len(velas) < period:
        last = velas[-1][1] if velas else 0.0
        return {'upper': last * 1.001, 'mid': last, 'lower': last * 0.999}
    closes = [v[1] for v in velas[-period:]]
    sma = sum(closes) / period
    variance = sum([(c - sma) ** 2 for c in closes]) / period
    std = variance ** 0.5
    return {'upper': sma + (std * std_dev), 'mid': sma, 'lower': sma - (std * std_dev)}

# ====================== DADOS ======================
def get_velas_kucoin(ativo, intervalo):
    try:
        params = {'symbol': ativo, 'type': intervalo, 'limit': NUM_VELAS_ANALISE_M1}
        r = requests.get(API_BASE_URL, params=params, timeout=8)
        r.raise_for_status()
        data = r.json().get('data', [])
        velas = [[float(v[1]), float(v[2]), float(v[3]), float(v[4])] for v in data]
        return velas
    except Exception as e:
        print(f"⚠️ Erro ao obter velas {ativo}: {e}")
        return []

# ====================== LÓGICA DE ASSERTIVIDADE ======================
def calcular_assertividade_confluencia(ativo, velas):
    if len(velas) < NUM_VELAS_ANALISE_M1:
        return {'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0}

    preco_entrada = velas[-1][1]
    rsi = calculate_rsi(velas)
    bb = calculate_bollinger_bands(velas)
    o1, c1 = velas[-1][0], velas[-1][1]
    o2, c2 = velas[-2][0], velas[-2][1]
    h_atual, l_atual = velas[-1][2], velas[-1][3]

    momentum_buy = (c1 > o1) and (c2 > o2)
    momentum_sell = (c1 < o1) and (c2 < o2)

    def checar_dir(direcao, momentum):
        if not momentum:
            return 0.0
        regras = 1
        if direcao == 'COMPRA' and l_atual <= bb['lower']:
            regras += 1
        if direcao == 'VENDA' and h_atual >= bb['upper']:
            regras += 1
        if direcao == 'COMPRA' and rsi <= RSI_OVERSOLD:
            regras += 1
        if direcao == 'VENDA' and rsi >= RSI_OVERBOUGHT:
            regras += 1
        return (regras / 3) * 100.0

    assert_buy = checar_dir('COMPRA', momentum_buy)
    assert_sell = checar_dir('VENDA', momentum_sell)
    final_sinal = 'NEUTRO 🟡'
    final_assertividade = max(assert_buy, assert_sell)
    if assert_buy >= ASSERTIVIDADE_MINIMA and assert_buy >= assert_sell:
        final_sinal = 'COMPRA FORTE 🚀'
    elif assert_sell >= ASSERTIVIDADE_MINIMA and assert_sell >= assert_buy:
        final_sinal = 'VENDA FORTE 📉'
    return {'sinal': final_sinal, 'assertividade': final_assertividade, 'preco_entrada': preco_entrada}

# ====================== CICLO PRINCIPAL ======================
def ciclo_analise():
    global ULTIMO_SINAL, ULTIMO_SINAL_CHECAR, ULTIMO_SINAL_REGISTRADO
    while True:
        try:
            melhor = {'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0}
            for ativo in ATIVOS_MONITORADOS:
                velas = get_velas_kucoin(ativo, INTERVALO_M1)
                analise = calcular_assertividade_confluencia(ativo, velas)
                if analise['assertividade'] >= melhor['assertividade']:
                    melhor = {'ativo': ativo, **analise}
            with state_lock:
                ULTIMO_SINAL.update({'horario': get_horario_brasilia().strftime('%H:%M:%S'), **melhor})
            print(f"📢 {ULTIMO_SINAL['horario']} - {ULTIMO_SINAL['ativo']}: {ULTIMO_SINAL['sinal']} ({ULTIMO_SINAL['assertividade']:.0f}%)")
            time.sleep(60)
        except Exception:
            traceback.print_exc()
            time.sleep(10)

Thread(target=ciclo_analise, daemon=True).start()

# ====================== STREAM SSE ======================
@app.route('/stream')
def stream():
    def event_stream():
        while True:
            with state_lock:
                yield f"data: {json.dumps(ULTIMO_SINAL)}\n\n"
            time.sleep(5)
    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/')
def index():
    return """
    <html>
    <head>
        <title>Robô Trader Confluência</title>
        <style>
            body { background:#0b0b0b; color:white; text-align:center; font-family:Arial; }
            h1 { color:#00ff88; }
            .info { font-size:18px; margin-top:20px; }
        </style>
    </head>
    <body>
        <h1>🤖 Robô Trader - Assertividade por Confluência</h1>
        <div id="dados">Carregando...</div>
        <script>
        const evt = new EventSource('/stream');
        evt.onmessage = function(e){
            const data = JSON.parse(e.data);
            document.getElementById('dados').innerHTML =
            `<div class='info'>
                <b>${data.horario}</b> | <b>${data.ativo}</b> <br>
                <span>${data.sinal}</span> <br>
                Assertividade: ${data.assertividade.toFixed(1)}%
            </div>`;
        }
        </script>
    </body>
    </html>
    """

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
