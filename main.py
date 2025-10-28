# main.py
# ROBÔ TRADER M1 AVANÇADO - DASHBOARD COM GRÁFICOS EM TEMPO REAL
# Painel moderno com gráficos de velas e momentum

import requests
import time
from datetime import datetime
from flask import Flask, render_template_string, jsonify
import threading

# --- CONFIGURAÇÕES ---
ATIVOS = ["BTC-USDT", "ETH-USDT"]
PERCENT_DIFF_BASE = 0.02
HISTORICO = []
ULTIMO_SINAL = {"ativo": None, "tipo": None, "hora": None, "resultado": None}
PRECOS_RECENTES = {at: [] for at in ATIVOS}  # para gráfico de velas

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

def media_movel(precos, periodo=3):
    if len(precos) < periodo:
        return sum(precos)/len(precos)
    return sum(precos[-periodo:])/periodo

def pressao_vela(preco_abertura, preco_fechamento):
    return "alta" if preco_fechamento > preco_abertura else "baixa"

def gerar_sinal(preco_atual, preco_anterior, momentum, pressao, mm):
    diff_adaptativo = max(PERCENT_DIFF_BASE/100, abs(preco_atual - mm)/mm)
    sinal = None
    if momentum > 0 and pressao == "alta":
        if (preco_atual - preco_anterior)/preco_anterior >= diff_adaptativo:
            sinal = "CALL"
    elif momentum < 0 and pressao == "baixa":
        if (preco_anterior - preco_atual)/preco_anterior >= diff_adaptativo:
            sinal = "PUT"
    return sinal

def checar_resultado(preco_entrada, preco_fechamento, tipo):
    if tipo == "CALL":
        return "WIN" if preco_fechamento > preco_entrada else "LOSS"
    elif tipo == "PUT":
        return "WIN" if preco_fechamento < preco_entrada else "LOSS"
    return None

# --- FLASK ---
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Robô M1 Tech Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background:#0d0d0d; color:#fff; font-family: 'Segoe UI', sans-serif; }
        h1 { text-align:center; color:#00ffff; }
        .panel { display:flex; justify-content:space-around; margin:10px 0; flex-wrap: wrap; }
        .card { background:#1a1a1a; padding:15px; border-radius:10px; margin:5px; flex:1 1 30%; box-shadow:0 0 15px #00ffff; text-align:center; }
        .sinal { font-weight:bold; }
        .WIN { color:#00ff00; font-weight:bold; }
        .LOSS { color:#ff4d4d; font-weight:bold; }
        .ativo { font-weight:bold; color:#00ffff; }
        .scroll { max-height:250px; overflow-y:auto; margin-top:10px; }
        table { width:100%; border-collapse: collapse; margin-top:10px; }
        th, td { padding:6px 8px; text-align:center; }
        th { background:#222; }
        tr:nth-child(even) { background:#1a1a1a; }
        canvas { background:#111; border-radius:10px; margin-top:15px; }
    </style>
    <meta http-equiv="refresh" content="2">
</head>
<body>
    <h1>Robô M1 Tech Dashboard</h1>
    <div class="panel">
        <div class="card">
            <h2>Último Sinal</h2>
            <p class="ativo">{{ ultimo_sinal.ativo }}</p>
            <p class="sinal">{{ ultimo_sinal.tipo }}</p>
            <p>{{ ultimo_sinal.hora }}</p>
            <p class="{{ ultimo_sinal.resultado }}">{{ ultimo_sinal.resultado }}</p>
        </div>
        <div class="card">
            <h2>Assertividade</h2>
            <p style="font-size:24px">{{ assertividade }}%</p>
            <p>Total Trades: {{ total_trades }}</p>
        </div>
    </div>
    <div class="scroll">
        <table border="1">
            <tr><th>Hora</th><th>Ativo</th><th>Sinal</th><th>Resultado</th></tr>
            {% for h in historico %}
            <tr>
                <td>{{ h.hora }}</td>
                <td class="ativo">{{ h.ativo }}</td>
                <td class="sinal">{{ h.sinal }}</td>
                <td class="{{ h.resultado }}">{{ h.resultado }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% for ativo in ativos %}
    <div class="card">
        <h3>{{ ativo }} - Mini Gráfico de Velas</h3>
        <canvas id="chart_{{ ativo }}"></canvas>
    </div>
    {% endfor %}
<script>
{% for ativo in ativos %}
var ctx = document.getElementById('chart_{{ ativo }}').getContext('2d');
var chart_{{ ativo }} = new Chart(ctx, {
    type: 'bar',
    data: {
        labels: {{ precos[ativo]['times']|safe }},
        datasets: [{
            label: 'Preço',
            data: {{ precos[ativo]['precos']|safe }},
            backgroundColor: {{ precos[ativo]['cores']|safe }},
            borderColor:'#00ffff',
            borderWidth:1
        }]
    },
    options: { scales: { y:{ beginAtZero:false, color:'#fff' }, x:{ color:'#fff' } }, plugins:{ legend:{ display:false } } }
});
{% endfor %}
</script>
</body>
</html>
"""

# --- LOOP ROBÔ ---
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
            PRECOS_RECENTES[ativo].append({'preco': preco_atual, 'hora': datetime.now().strftime("%H:%M:%S")})
            if len(precos_anteriores[ativo])>10:
                precos_anteriores[ativo].pop(0)
            if len(PRECOS_RECENTES[ativo])>10:
                PRECOS_RECENTES[ativo].pop(0)

            momentum = calcular_momentum(precos_anteriores[ativo])
            mm = media_movel(precos_anteriores[ativo], periodo=3)
            pressao = pressao_vela(precos_abertura[ativo], preco_atual)
            preco_anterior = precos_anteriores[ativo][-2] if len(precos_anteriores[ativo])>1 else preco_atual

            sinal = gerar_sinal(preco_atual, preco_anterior, momentum, pressao, mm)
            if sinal:
                hora_sinal = datetime.now().strftime("%H:%M:%S")
                time.sleep(50)
                preco_fechamento = pegar_preco(ativo)
                resultado = checar_resultado(preco_atual, preco_fechamento, sinal)

                HISTORICO.append({"ativo": ativo, "sinal": sinal, "hora": hora_sinal, "resultado": resultado})
                ULTIMO_SINAL.update({"ativo": ativo, "tipo": sinal, "hora": hora_sinal, "resultado": resultado})

            if datetime.now().second >= 55:
                precos_abertura[ativo] = None
        time.sleep(1)

# --- ROUTE DASHBOARD ---
@app.route("/")
def dashboard():
    total_trades = len(HISTORICO)
    wins = sum(1 for h in HISTORICO if h["resultado"]=="WIN")
    assertividade = round((wins/total_trades)*100,1) if total_trades>0 else 0

    precos_formatados = {}
    for at in ATIVOS:
        precos_formatados[at] = {
            'precos':[p['preco'] for p in PRECOS_RECENTES[at]],
            'times':[p['hora'] for p in PRECOS_RECENTES[at]],
            'cores':['#00ff00' if p['preco']>=PRECOS_RECENTES[at][0]['preco'] else '#ff4d4d' for p in PRECOS_RECENTES[at]]
        }

    return render_template_string(HTML_TEMPLATE,
                                  historico=HISTORICO[::-1],
                                  ultimo_sinal=ULTIMO_SINAL,
                                  assertividade=assertividade,
                                  total_trades=total_trades,
                                  ativos=ATIVOS,
                                  precos=precos_formatados)

# --- INÍCIO ---
if __name__=="__main__":
    t = threading.Thread(target=loop_robo)
    t.start()
    import os
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0", port=port)
