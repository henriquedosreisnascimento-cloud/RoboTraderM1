# main.py
# ROBÔ TRADER M1 (WEB) - ASSERTIVIDADE POR CONFLUÊNCIA (3 REGRAS)
# Flask + SSE + Price Action com RSI e Bandas de Bollinger
# ✅ Pronto para rodar no Render, Replit ou GitHub Codespaces

from flask import Flask, Response
import requests
import time
from datetime import datetime
import pytz
from threading import Thread, Lock
import traceback
import copy
import json

# ====================== CONFIGURAÇÕES ======================
TIMEZONE_BR = 'America/Sao_Paulo'
ATIVOS_MONITORADOS = ['BTC-USDT', 'ETH-USDT']
API_BASE_URL = 'https://api.kucoin.com/api/v1/market/candles'
INTERVALO_M1 = '1min'
NUM_VELAS_ANALISE_M1 = 30
ASSERTIVIDADE_MINIMA = 100.0
MAX_HISTORICO = 10
PERCENTUAL_SL_TP = 0.0005
PERIOD_BB = 14
STD_DEV_BB = 2
PERIOD_RSI = 14
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0

app = Flask(__name__)
state_lock = Lock()

# ====================== VARIÁVEIS DE ESTADO ======================
def get_horario_brasilia():
    return datetime.now(pytz.timezone(TIMEZONE_BR))

ULTIMO_SINAL = {'horario': '00:00:00', 'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0}
ULTIMO_SINAL_REGISTRADO = {'horario': 'N/A', 'sinal_tipo': 'N/A'}
HISTORICO_SINAIS = []
ULTIMO_SINAL_CHECAR = None

# ====================== INDICADORES ======================
def calculate_rsi(velas, period=PERIOD_RSI):
    if len(velas) < period:
        return 50.0
    closes = [v[1] for v in velas]
    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [c for c in changes if c > 0]
    losses = [abs(c) for c in changes if c < 0]
    avg_gain = sum(gains)/period if gains else 0
    avg_loss = sum(losses)/period if losses else 0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain/avg_loss
    return 100 - (100 / (1 + rs))

def calculate_bollinger_bands(velas, period=PERIOD_BB, std_dev=STD_DEV_BB):
    if len(velas) < period:
        last = velas[-1][1]
        return {'upper': last*1.001, 'mid': last, 'lower': last*0.999}
    closes = [v[1] for v in velas[-period:]]
    sma = sum(closes)/period
    variance = sum([(c - sma)**2 for c in closes]) / period
    std = variance ** 0.5
    return {'upper': sma + std*std_dev, 'mid': sma, 'lower': sma - std*std_dev}

# ====================== BUSCA DE DADOS ======================
def get_velas_kucoin(ativo, intervalo):
    try:
        params = {'symbol': ativo, 'type': intervalo, 'limit': NUM_VELAS_ANALISE_M1}
        r = requests.get(API_BASE_URL, params=params, timeout=8)
        data = r.json().get('data', [])
        velas = [[float(v[1]), float(v[2]), float(v[3]), float(v[4])] for v in data]
        return velas[::-1]  # Ordena cronologicamente
    except Exception as e:
        print(f"Erro ao obter velas {ativo}: {e}")
        return []

# ====================== ASSERTIVIDADE ======================
def calcular_assertividade_confluencia(ativo, velas):
    if len(velas) < NUM_VELAS_ANALISE_M1:
        return {'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0}
    
    preco_entrada = velas[-1][1]
    h_atual = velas[-1][2]
    l_atual = velas[-1][3]
    rsi_val = calculate_rsi(velas)
    bb = calculate_bollinger_bands(velas)
    o1, c1 = velas[-1][0], velas[-1][1]
    o2, c2 = velas[-2][0], velas[-2][1]
    momentum_buy = (c1 > o1) and (c2 > o2)
    momentum_sell = (c1 < o1) and (c2 < o2)

    def check(direcao, has_momentum):
        if not has_momentum: return 0.0
        passed = 1
        if direcao == 'COMPRA' and l_atual <= bb['lower']: passed += 1
        if direcao == 'VENDA' and h_atual >= bb['upper']: passed += 1
        if direcao == 'COMPRA' and rsi_val <= RSI_OVERSOLD: passed += 1
        if direcao == 'VENDA' and rsi_val >= RSI_OVERBOUGHT: passed += 1
        return (passed / 3.0) * 100.0

    assert_buy = check('COMPRA', momentum_buy)
    assert_sell = check('VENDA', momentum_sell)
    if assert_buy >= ASSERTIVIDADE_MINIMA: return {'sinal': 'COMPRA FORTE 🚀', 'assertividade': assert_buy, 'preco_entrada': preco_entrada}
    if assert_sell >= ASSERTIVIDADE_MINIMA: return {'sinal': 'VENDA FORTE 📉', 'assertividade': assert_sell, 'preco_entrada': preco_entrada}
    return {'sinal': 'NEUTRO 🟡', 'assertividade': max(assert_buy, assert_sell), 'preco_entrada': preco_entrada}

# ====================== CICLO DE ANÁLISE ======================
def ciclo_analise():
    global ULTIMO_SINAL
    while True:
        try:
            melhor = {'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0}
            for ativo in ATIVOS_MONITORADOS:
                velas = get_velas_kucoin(ativo, INTERVALO_M1)
                analise = calcular_assertividade_confluencia(ativo, velas)
                if analise['assertividade'] > melhor['assertividade']:
                    melhor = {'ativo': ativo, **analise}
            with state_lock:
                ULTIMO_SINAL.update({'horario': get_horario_brasilia().strftime('%H:%M:%S'), **melhor})
            print(f"[{ULTIMO_SINAL['horario']}] {ULTIMO_SINAL['ativo']} - {ULTIMO_SINAL['sinal']} ({ULTIMO_SINAL['assertividade']:.0f}%)")
        except Exception:
            traceback.print_exc()
        time.sleep(60)

Thread(target=ciclo_analise, daemon=True).start()

# ====================== SSE STREAM ======================
def event_stream():
    while True:
        with state_lock:
            payload = {
                'time': get_horario_brasilia().strftime('%H:%M:%S'),
                'ativo': ULTIMO_SINAL['ativo'],
                'sinal': ULTIMO_SINAL['sinal'],
                'assertividade': ULTIMO_SINAL['assertividade'],
                'preco': ULTIMO_SINAL['preco_entrada']
            }
        yield f"data: {json.dumps(payload)}\n\n"
        time.sleep(5)

@app.route("/stream")
def stream():
    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/")
def home():
    return """
    <html>
    <head>
        <title>Robô Trader M1 - Dashboard SSE</title>
        <meta charset='utf-8'>
        <style>
            body { font-family: Arial; background:#111; color:#fff; text-align:center; padding:20px; }
            .box { padding:20px; border-radius:15px; margin:auto; width:60%; background:#222; }
            .compra { color:#00ff88; }
            .venda { color:#ff5555; }
            .neutro { color:#ffff55; }
        </style>
    </head>
    <body>
        <h1>📊 ROBÔ TRADER M1 - SSE</h1>
        <div class='box' id='info'>Conectando...</div>
        <script>
        const evt = new EventSource('/stream');
        evt.onmessage = function(e) {
            const d = JSON.parse(e.data);
            let cls = 'neutro';
            if(d.sinal.includes('COMPRA')) cls = 'compra';
            if(d.sinal.includes('VENDA')) cls = 'venda';
            document.getElementById('info').innerHTML = `
                <h2 class='${cls}'>${d.sinal}</h2>
                <p><b>Ativo:</b> ${d.ativo}</p>
                <p><b>Preço:</b> ${d.preco.toFixed(5)}</p>
                <p><b>Assertividade:</b> ${d.assertividade.toFixed(1)}%</p>
                <p><b>Hora:</b> ${d.time}</p>`;
        }
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
