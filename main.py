# main.py
# ROBÔ TRADER M1 (FLUIDO) - ESTRATÉGIA RSI/BB/MOMENTUM
# Combina a paralelização de I/O da v3 com um Dashboard esteticamente aprimorado

from flask import Flask, Response, render_template_string
import requests
import time
from datetime import datetime
import pytz
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor
import os
import copy
import traceback
import json

# ====================== CONFIGURAÇÕES ======================
TIMEZONE_BR = 'America/Sao_Paulo'
ATIVOS_MONITORADOS = ['BTC-USDT', 'ETH-USDT', 'EUR-USDT', 'DOT-USDT', 'ADA-USDT'] 
API_BASE_URL = 'https://api.kucoin.com/api/v1/market/candles'
INTERVALO_M1 = '1min'
NUM_VELAS_ANALISE_M1 = 30 
MAX_WORKERS = 5

ASSERTIVIDADE_MINIMA = 80.0
MAX_HISTORICO = 10
PERCENTUAL_SL_TP = 0.0005 

PERIOD_BB = 14
STD_DEV_BB = 2
PERIOD_RSI = 14
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0

DASHBOARD_REFRESH_RATE_SECONDS = 5 
URL_ALERTE_SONORO = "https://www.soundhelix.com/examples/audio/Wave-beep.wav"

# ====================== INICIALIZAÇÃO DO FLASK ======================
app = Flask(__name__)
state_lock = Lock()

# ====================== VARIÁVEIS GLOBAIS ======================
def get_horario_brasilia():
    fuso_brasil = pytz.timezone(TIMEZONE_BR)
    return datetime.now(fuso_brasil)

ULTIMO_SINAL = {'horario': get_horario_brasilia().strftime('%H:%M:%S'),
                'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0}

ULTIMO_SINAL_REGISTRADO = {'horario': 'N/A', 'sinal_tipo': 'N/A'}
HISTORICO_SINAIS = []
ULTIMO_SINAL_CHECAR = None

# ====================== FUNÇÕES DE INDICADORES ======================
def calculate_rsi(velas, period=PERIOD_RSI):
    if len(velas) < period: return 50.0
    closes = [v[1] for v in velas]
    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    initial_gains = [c for c in changes[0:period] if c > 0]
    initial_losses = [abs(c) for c in changes[0:period] if c < 0]
    avg_gain = sum(initial_gains) / period if initial_gains else 0
    avg_loss = sum(initial_losses) / period if initial_losses else 0
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_bollinger_bands(velas, period=PERIOD_BB, std_dev=STD_DEV_BB):
    if len(velas) < period:
        last_close = velas[-1][1] if velas else 0.0
        return {'upper': last_close * 1.001, 'mid': last_close, 'lower': last_close * 0.999} 
    closes = [v[1] for v in velas[-period:]]
    sma = sum(closes) / period
    variance = sum([(c - sma) ** 2 for c in closes]) / period
    std_dev_val = variance ** 0.5
    return {'upper': sma + std_dev_val * std_dev, 'mid': sma, 'lower': sma - std_dev_val * std_dev}

# ====================== FUNÇÕES DE DADOS ======================
def get_velas_kucoin(ativo, intervalo, limit=NUM_VELAS_ANALISE_M1):
    try:
        params = {'symbol': ativo, 'type': intervalo, 'limit': limit} 
        r = requests.get(API_BASE_URL, params=params, timeout=8)
        r.raise_for_status()
        data = r.json().get('data', [])
        velas = [[float(v[1]), float(v[2]), float(v[3]), float(v[4])] for v in data]
        return velas
    except Exception as e:
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Erro ao obter velas {intervalo} de {ativo}: {e}")
        return []

def checar_resultado_sinal(sinal_checar):
    global HISTORICO_SINAIS
    try:
        ativo = sinal_checar['ativo']
        preco_entrada = sinal_checar['preco_entrada']
        direcao_sinal = sinal_checar['sinal']
        if ativo == 'N/A' or 'NEUTRO' in direcao_sinal or sinal_checar['assertividade'] < ASSERTIVIDADE_MINIMA:
            return
        velas_exp = get_velas_kucoin(ativo, INTERVALO_M1, limit=1)
        if len(velas_exp) < 1: return
        o_exp, c_exp, h_exp, l_exp = velas_exp[-1]
        resultado = 'NEUTRO'
        percentual_sl_tp = PERCENTUAL_SL_TP
        if 'COMPRA' in direcao_sinal:
            tp_price = preco_entrada * (1 + percentual_sl_tp)
            sl_price = preco_entrada * (1 - percentual_sl_tp)
            if h_exp >= tp_price: resultado = 'WIN ✅ (TP)'
            elif l_exp <= sl_price: resultado = 'LOSS ❌ (SL)'
            else: resultado = 'WIN ✅ (Close)' if c_exp > preco_entrada else 'LOSS ❌ (Close)'
        elif 'VENDA' in direcao_sinal:
            tp_price = preco_entrada * (1 - percentual_sl_tp)
            sl_price = preco_entrada * (1 + percentual_sl_tp)
            if l_exp <= tp_price: resultado = 'WIN ✅ (TP)'
            elif h_exp >= sl_price: resultado = 'LOSS ❌ (SL)'
            else: resultado = 'WIN ✅ (Close)' if c_exp < preco_entrada else 'LOSS ❌ (Close)'
        with state_lock:
            HISTORICO_SINAIS.append({
                'horario': sinal_checar['horario'],
                'ativo': ativo,
                'sinal': direcao_sinal,
                'assertividade': sinal_checar['assertividade'],
                'resultado': resultado,
                'preco_entrada': preco_entrada,
                'preco_expiracao': c_exp
            })
            if len(HISTORICO_SINAIS) > MAX_HISTORICO: HISTORICO_SINAIS.pop(0)
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] 🎯 Resultado de {ativo}: {resultado}")
    except Exception:
        traceback.print_exc()

def calcular_assertividade_historico():
    with state_lock:
        if not HISTORICO_SINAIS: return {'total': 0, 'wins': 0, 'losses': 0, 'percentual': 'N/A'}
        wins = sum(1 for item in HISTORICO_SINAIS if 'WIN' in item['resultado'])
        total = len(HISTORICO_SINAIS)
        losses = total - wins
        percentual = f"{(wins / total) * 100:.2f}%" if total else 'N/A'
        return {'total': total, 'wins': wins, 'losses': losses, 'percentual': percentual}

def formatar_historico_html(historico):
    linhas_html = []
    for item in reversed(historico):
        classe = 'win' if 'WIN' in item['resultado'] else 'loss'
        diferenca = item['preco_expiracao'] - item['preco_entrada']
        sinal_diff = "+" if diferenca >= 0 else ""
        resultado_formatado = item['resultado'].replace(' (Close)', '')
        linha = f"[{item['horario']}] {item['ativo']} -> <span class='{classe}'>{resultado_formatado}</span> (Assertividade: {item['assertividade']:.0f}%. Diff: {sinal_diff}{diferenca:.5f})"
        linhas_html.append(linha)
    return '\n'.join(linhas_html)

# ====================== ESTRATÉGIA CENTRAL ======================
def analisar_ativo(ativo):
    velas_m1 = get_velas_kucoin(ativo, INTERVALO_M1)
    if not velas_m1 or len(velas_m1) < NUM_VELAS_ANALISE_M1:
        return {'ativo': ativo, 'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0}
    preco_entrada = velas_m1[-1][1]
    o_atual, c_atual, h_atual, l_atual = velas_m1[-1]
    o2, c2 = velas_m1[-2][0], velas_m1[-2][1]
    rsi_val = calculate_rsi(velas_m1)
    bb_bands = calculate_bollinger_bands(velas_m1)
    momentum_buy = (c_atual > o_atual) and (c2 > o2)
    momentum_sell = (c_atual < o_atual) and (c2 < o2)
    def check_direction_confluence(direcao, has_momentum):
        passed_rules = 0
        if has_momentum: passed_rules += 1
        else: return 0.0
        if direcao == 'COMPRA' and l_atual <= bb_bands['lower']: passed_rules += 1
        elif direcao == 'VENDA' and h_atual >= bb_bands['upper']: passed_rules += 1
        if direcao == 'COMPRA' and rsi_val <= RSI_OVERSOLD: passed_rules += 1
        elif direcao == 'VENDA' and rsi_val >= RSI_OVERBOUGHT: passed_rules += 1
        return (passed_rules / 3.0) * 100.0
    assert_buy = check_direction_confluence('COMPRA', momentum_buy)
    assert_sell = check_direction_confluence('VENDA', momentum_sell)
    final_sinal = 'NEUTRO 🟡'
    final_assertividade = 0.0
    if assert_buy >= ASSERTIVIDADE_MINIMA and assert_buy >= assert_sell:
        final_sinal = 'COMPRA APROVADA ✅'
        final_assertividade = assert_buy
    elif assert_sell >= ASSERTIVIDADE_MINIMA and assert_sell >= assert_buy:
        final_sinal = 'VENDA APROVADA ❌'
        final_assertividade = assert_sell
    else:
        final_assertividade = max(assert_buy, assert_sell)
        final_sinal = 'ENTRADA BLOQUEADA' if final_assertividade > 0 else 'NEUTRO 🟡'
    return {'ativo': ativo, 'sinal': final_sinal, 'assertividade': final_assertividade, 'preco_entrada': preco_entrada}

# ====================== CICLO DE ANÁLISE ======================
def ciclo_analise():
    global ULTIMO_SINAL, ULTIMO_SINAL_CHECAR, ULTIMO_SINAL_REGISTRADO
    time.sleep(1)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        while True:
            try:
                now_dt = get_horario_brasilia()
                seconds_until_next_minute = 60 - now_dt.second
                sleep_time = seconds_until_next_minute if seconds_until_next_minute != 60 else 60
                if ULTIMO_SINAL_CHECAR:
                    checar_resultado_sinal(ULTIMO_SINAL_CHECAR)
                    with state_lock:
                        ULTIMO_SINAL_CHECAR = None
                time.sleep(sleep_time)
                start_time = time.time()
                now_dt = get_horario_brasilia()
                horario_atual_str = now_dt.strftime('%H:%M:%S')
                print(f"[{horario_atual_str}] Iniciando novo ciclo de análise (PARALELO)...")
                melhores_sinais = list(executor.map(analisar_ativo, ATIVOS_MONITORADOS))
                melhor = {'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0}
                for sinal in melhores_sinais:
                    if sinal['assertividade'] >= melhor['assertividade']:
                        melhor = sinal
                end_time = time.time()
                print(f"[{horario_atual_str}] Ciclo concluído em {end_time - start_time:.2f} segundos.")
                sinal_final = {
                    'horario': horario_atual_str,
                    'ativo': melhor['ativo'],
                    'sinal': melhor['sinal'],
                    'assertividade': melhor['assertividade'],
                    'preco_entrada': melhor['preco_entrada']
                }
                with state_lock:
                    if sinal_final['assertividade'] >= ASSERTIVIDADE_MINIMA:
                        ULTIMO_SINAL_CHECAR = copy.deepcopy(sinal_final)
                        ULTIMO_SINAL_REGISTRADO.update({
                            'horario': sinal_final['horario'],
                            'sinal_tipo': 'COMPRA' if 'COMPRA' in sinal_final['sinal'] else 'VENDA'
                        })
                    ULTIMO_SINAL.update(sinal_final)
                print(f"[{horario_atual_str}] 📢 Novo Sinal: {ULTIMO_SINAL['ativo']} - {ULTIMO_SINAL['sinal']} (Assertividade: {ULTIMO_SINAL['assertividade']:.0f}%)")
            except Exception:
                traceback.print_exc()
                time.sleep(5)

analysis_thread = Thread(target=ciclo_analise, daemon=True)
analysis_thread.start()

# ====================== SSE FLASK ======================
@app.route('/dashboard/stream')
def dashboard_stream():
    def event_stream():
        while True:
            try:
                with state_lock:
                    data = {
                        'sinal': ULTIMO_SINAL['sinal'],
                        'ativo': ULTIMO_SINAL['ativo'],
                        'assertividade': ULTIMO_SINAL['assertividade'],
                        'preco_entrada': ULTIMO_SINAL['preco_entrada'],
                        'historico': formatar_historico_html(HISTORICO_SINAIS)
                    }
                yield f"data: {json.dumps(data)}\n\n"
                time.sleep(DASHBOARD_REFRESH_RATE_SECONDS)
            except GeneratorExit:
                break
    return Response(event_stream(), mimetype='text/event-stream')

@app.route('/')
def dashboard_home():
    html_template = """
    <html>
    <head>
    <title>Robô Trader M1 Dashboard</title>
    <style>
    body { font-family: Arial; background-color: #111; color: #eee; padding: 20px; }
    .win { color: #0f0; }
    .loss { color: #f00; }
    </style>
    <script>
    const evtSource = new EventSource("/dashboard/stream");
    evtSource.onmessage = function(e) {
        const data = JSON.parse(e.data);
        document.getElementById("sinal").innerText = data.sinal + " (" + data.ativo + ")";
        document.getElementById("assertividade").innerText = data.assertividade + "%";
        document.getElementById("preco").innerText = data.preco_entrada.toFixed(5);
        document.getElementById("historico").innerHTML = data.historico;
    }
    </script>
    </head>
    <body>
    <h1>Robô Trader M1 Dashboard</h1>
    <div>Sinal Atual: <span id="sinal">-</span></div>
    <div>Assertividade: <span id="assertividade">0%</span></div>
    <div>Preço de Entrada: <span id="preco">0.00000</span></div>
    <h2>Histórico de Sinais:</h2>
    <pre id="historico"></pre>
    </body>
    </html>
    """
    return render_template_string(html_template)

# ====================== RODANDO O APP ======================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
