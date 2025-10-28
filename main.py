# main.py
# ROBÔ TRADER M1 (WEB) - DASHBOARD VISUAL COM GRÁFICO
# Inclui sinais fortes, gráfico de velas em tempo real e histórico colorido

from flask import Flask, Response, jsonify
import requests
import time
from datetime import datetime
import pytz
from threading import Thread
import os
import copy
import traceback
import json

# ====================== CONFIGURAÇÕES ======================
TIMEZONE_BR = 'America/Sao_Paulo'
ATIVOS_MONITORADOS = ['BTC-USDT', 'ETH-USDT', 'EUR-USDT']
API_BASE_URL = 'https://api.kucoin.com/api/v1/market/candles'
INTERVALO = '1min'
NUM_VELAS_ANALISE = 30  # para o gráfico
SCORE_MINIMO_SINAL = 2.0
MAX_HISTORICO = 10
URL_ALERTE_SONORO = "https://www.soundhelix.com/examples/audio/Wave-beep.wav"

# ====================== INICIALIZAÇÃO DO FLASK ======================
app = Flask(__name__)

# ====================== VARIÁVEIS GLOBAIS ======================
def get_horario_brasilia():
    fuso_brasil = pytz.timezone(TIMEZONE_BR)
    return datetime.now(fuso_brasil)

ULTIMO_SINAL = {
    'horario': get_horario_brasilia().strftime('%H:%M:%S'),
    'ativo': 'N/A',
    'sinal': 'NEUTRO 🟡',
    'score': 0,
    'preco_entrada': 0.0
}

ULTIMO_SINAL_REGISTRADO = {'horario': 'N/A', 'sinal_tipo': 'N/A'}
HISTORICO_SINAIS = []
ULTIMO_SINAL_CHECAR = None
VELAS_ATIVOS = {ativo: [] for ativo in ATIVOS_MONITORADOS}

# ====================== FUNÇÕES BASE ======================
def calcular_assertividade():
    if not HISTORICO_SINAIS:
        return {'total': 0, 'wins': 0, 'losses': 0, 'percentual': 'N/A'}
    wins = sum(1 for item in HISTORICO_SINAIS if item['resultado'] == 'WIN ✅')
    total = len(HISTORICO_SINAIS)
    losses = total - wins
    percentual = f"{(wins/total)*100:.2f}%" if total else 'N/A'
    return {'total': total, 'wins': wins, 'losses': losses, 'percentual': percentual}

def get_ultimas_velas(ativo):
    global VELAS_ATIVOS
    try:
        params = {'symbol': ativo, 'type': INTERVALO}
        r = requests.get(API_BASE_URL, params=params, timeout=8)
        r.raise_for_status()
        data = r.json().get('data', [])

        velas = []
        for v in data[-NUM_VELAS_ANALISE:]:
            velas.append({
                'time': int(v[0])//1000,  # timestamp em segundos
                'open': float(v[1]),
                'close': float(v[2]),
                'high': float(v[3]),
                'low': float(v[4])
            })
        VELAS_ATIVOS[ativo] = velas
        return velas
    except Exception as e:
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Erro ao obter velas de {ativo}: {e}")
        return VELAS_ATIVOS.get(ativo, [])

def analisar_price_action(velas):
    if len(velas) < 2:
        return {'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}
    o1, c1 = velas[-1]['open'], velas[-1]['close']
    o2, c2 = velas[-2]['open'], velas[-2]['close']
    score = 0
    if c1 > o1: score += 1
    elif c1 < o1: score -= 1
    if c2 > o2: score += 1
    elif c2 < o2: score -= 1

    if score >= SCORE_MINIMO_SINAL:
        sinal_emoji = 'COMPRA FORTE 🚀'
    elif score <= -SCORE_MINIMO_SINAL:
        sinal_emoji = 'VENDA FORTE 📉'
    elif score > 0:
        sinal_emoji = 'COMPRA Fraca 🟢'
    elif score < 0:
        sinal_emoji = 'VENDA Fraca 🔴'
    else:
        sinal_emoji = 'NEUTRO 🟡'
    return {'sinal': sinal_emoji, 'score': score, 'preco_entrada': c1}

def checar_resultado_sinal(sinal_checar):
    global HISTORICO_SINAIS
    try:
        ativo = sinal_checar['ativo']
        preco_entrada = sinal_checar['preco_entrada']
        direcao_sinal = sinal_checar['sinal']
        if ativo == 'N/A' or 'NEUTRO' in direcao_sinal: return
        velas = get_ultimas_velas(ativo)
        if len(velas) < 1: return
        c_exp = velas[-1]['close']
        resultado = 'NEUTRO'
        if 'COMPRA' in direcao_sinal: resultado = 'WIN ✅' if c_exp > preco_entrada else 'LOSS ❌'
        elif 'VENDA' in direcao_sinal: resultado = 'WIN ✅' if c_exp < preco_entrada else 'LOSS ❌'

        HISTORICO_SINAIS.append({
            'horario': sinal_checar['horario'],
            'ativo': ativo,
            'sinal': direcao_sinal,
            'resultado': resultado,
            'preco_entrada': preco_entrada,
            'preco_expiracao': c_exp
        })
        if len(HISTORICO_SINAIS) > MAX_HISTORICO: HISTORICO_SINAIS.pop(0)
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] 🎯 Resultado de {ativo}: {resultado}")
    except Exception:
        print("Erro em checar_resultado_sinal:")
        traceback.print_exc()

def formatar_historico_html(historico):
    linhas_html = []
    for item in reversed(historico):
        classe = 'win' if 'WIN' in item['resultado'] else 'loss'
        linha = f"[{item['horario']}] {item['ativo']} -> <span class='{classe}'>{item['resultado']}</span> (Sinal: {item['sinal']})"
        linhas_html.append(linha)
    return '\n'.join(linhas_html)

# ====================== CICLO DE ANÁLISE (BACKGROUND) ======================
def ciclo_analise():
    global ULTIMO_SINAL, ULTIMO_SINAL_CHECAR, ULTIMO_SINAL_REGISTRADO
    time.sleep(1)
    while True:
        try:
            horario_atual_dt = get_horario_brasilia()
            horario_atual_str = horario_atual_dt.strftime('%H:%M:%S')

            if ULTIMO_SINAL_CHECAR:
                checar_resultado_sinal(ULTIMO_SINAL_CHECAR)
                ULTIMO_SINAL_CHECAR = None

            melhor = {'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}
            for ativo in ATIVOS_MONITORADOS:
                velas = get_ultimas_velas(ativo)
                analise = analisar_price_action(velas)
                if abs(analise['score']) >= abs(melhor['score']):
                    melhor = {'ativo': ativo, **analise}

            if abs(melhor['score']) == SCORE_MINIMO_SINAL:
                sinal_final = melhor
                sinal_final['horario'] = horario_atual_str
                ULTIMO_SINAL_CHECAR = copy.deepcopy(sinal_final)
                ULTIMO_SINAL_REGISTRADO = {
                    'horario': sinal_final['horario'],
                    'sinal_tipo': 'COMPRA' if 'COMPRA' in sinal_final['sinal'] else 'VENDA'
                }
            else:
                sinal_final = {'horario': horario_atual_str,'ativo':'N/A','sinal':'NEUTRO 🟡','score':0,'preco_entrada':0.0}

            ULTIMO_SINAL.update({
                'horario': sinal_final['horario'],
                'ativo': sinal_final['ativo'],
                'sinal': sinal_final['sinal'],
                'score': sinal_final['score'],
                'preco_entrada': sinal_final['preco_entrada']
            })
        except Exception:
            print("Erro no ciclo_analise:")
            traceback.print_exc()
        time.sleep(60)

Thread(target=ciclo_analise, daemon=True).start()

# ====================== ROTAS ======================
@app.route('/')
def home():
    try:
        horario_atual_brasilia = get_horario_brasilia().strftime('%H:%M:%S')
        sinal_exibicao = ULTIMO_SINAL['sinal']
        historico_html = formatar_historico_html(HISTORICO_SINAIS)
        assertividade_data = calcular_assertividade()
        ultimo_sinal_hora = ULTIMO_SINAL_REGISTRADO['horario']
        ultimo_sinal_tipo = ULTIMO_SINAL_REGISTRADO['sinal_tipo']

        if ultimo_sinal_tipo == 'COMPRA': ultimo_sinal_cor_css = 'var(--compra-borda)'; ultimo_sinal_texto=f'✅ Última Entrada: COMPRA ({ultimo_sinal_hora})'
        elif ultimo_sinal_tipo == 'VENDA': ultimo_sinal_cor_css = 'var(--venda-borda)'; ultimo_sinal_texto=f'❌ Última Entrada: VENDA ({ultimo_sinal_hora})'
        else: ultimo_sinal_cor_css='var(--neutro-borda)'; ultimo_sinal_texto='🟡 Nenhuma Entrada Forte Registrada'

        velas_json = json.dumps(VELAS_ATIVOS.get(ULTIMO_SINAL['ativo'], []))

        html_content = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>ROBÔ TRADER M1 - Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {{
--bg-primary:#1C2331; --bg-secondary:#2A3346; --text-primary:#DCE3F4;
--neutro-fundo:#374257; --neutro-borda:#4D5970;
--compra-fundo:#2D4C42; --compra-borda:#6AA84F;
--venda-fundo:#5C3A3A; --venda-borda:#E06666;
--assert-fundo:#3B3F50; --assert-borda:#FFC107;
}}
body {{ background-color:var(--bg-primary); color:var(--text-primary); font-family:'Poppins', sans-serif; padding:10px; }}
.container {{ max-width:1000px; margin:20px auto; background-color:var(--bg-secondary); padding:20px; border-radius:20px; }}
h1 {{ color:#70A0FF; text-align:center; }}
#chart {{ height:350px; width:100%; margin-bottom:20px; }}
.last-signal-box {{ background-color:#3B3F50; border-left:5px solid {ultimo_sinal_cor_css}; padding:10px; border-radius:8px; margin-bottom:20px; text-align:center; }}
.sinal-box {{ padding:15px; background-color:var(--neutro-fundo); border-radius:12px; margin-bottom:20px; }}
.assertividade-box {{ padding:15px; background-color:var(--assert-fundo); border-radius:12px; text-align:center; margin-bottom:20px; }}
pre {{ background-color:#101520; padding:15px; border-radius:12px; overflow:auto; color:#B0B0B0; }}
.win {{ color:var(--compra-borda); font-weight:700; }}
.loss {{ color:var(--venda-borda); font-weight:700; }}
</style>
</head>
<body>
<audio id="alertaAudio" src="{URL_ALERTE_SONORO}" preload="auto"></audio>
<div class="container">
<h1>ROBÔ TRADER M1 | DASHBOARD SNIPER</h1>
<div class="last-signal-box">{ultimo_sinal_texto}</div>
<div id="chart"></div>
<div class="sinal-box">
<strong>Sinal Atual:</strong> {sinal_exibicao} - Ativo: {ULTIMO_SINAL['ativo']} - Score: {ULTIMO_SINAL['score']}
</div>
<div class="assertividade-box">
<p>Assertividade: {assertividade_data['percentual']}</p>
<p>Wins: {assertividade_data['wins']} / Total: {assertividade_data['total']}</p>
</div>
<h2>Histórico de Sinais</h2>
<pre>{historico_html or 'Nenhum registro ainda.'}</pre>
</div>

<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
<script>
const chart = LightweightCharts.createChart(document.getElementById('chart'), {{
    width: document.getElementById('chart').offsetWidth,
    height: 350,
    layout: {{ backgroundColor:'#1C2331', textColor:'#DCE3F4' }},
    grid: {{ vertLines: {{ color:'#2A3346' }}, horzLines: {{ color:'#2A3346' }} }}
}});
const candleSeries = chart.addCandlestickSeries();
const velas = {velas_json};
const chartData = velas.map(v => {{
    return {{ time: v.time, open: v.open, high: v.high, low: v.low, close: v.close }};
}});
candleSeries.setData(chartData);

document.addEventListener('click', function() {{
    var audio = document.getElementById('alertaAudio');
    if(audio) {{ audio.volume=0.8; audio.play().catch(e=>console.log(e)); }}
}});
</script>
</body>
</html>"""
        return Response(html_content, mimetype='text/html')
    except Exception:
        print("Erro ao gerar dashboard:")
        traceback.print_exc()
        return Response("<h1>Erro ao gerar dashboard</h1><pre>"+traceback.format_exc()+"</pre>", mimetype='text/html')

# ====================== RODAR A APLICAÇÃO ======================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
