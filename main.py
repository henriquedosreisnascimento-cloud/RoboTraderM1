# main.py
# ROBÔ TRADER M1 (WEB) - DASHBOARD VISUAL FUNCIONAL
# Inclui sinais fortes, gráfico de velas atualizado via API, histórico e assertividade

from flask import Flask, Response, jsonify
import requests
import time
from datetime import datetime
import pytz
from threading import Thread
import os
import copy
import traceback

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
                'time': int(v[0])//1000,  # timestamp em segundos para JS
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

# ====================== CICLO DE ANÁLISE ======================
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
@app.route('/api/velas')
def api_velas():
    try:
        ativo = ULTIMO_SINAL['ativo']
        velas = VELAS_ATIVOS.get(ativo, [])
        return jsonify(velas)
    except Exception:
        return jsonify([])

@app.route('/')
def home():
    try:
        horario_atual_brasilia = get_horario_brasilia().strftime('%H:%M:%S')
        sinal_exibicao = ULTIMO_SINAL['sinal']
        ultimo_sinal_hora = ULTIMO_SINAL_REGISTRADO['horario']
        ultimo_sinal_tipo = ULTIMO_SINAL_REGISTRADO['sinal_tipo']

        if ultimo_sinal_tipo == 'COMPRA':
            ultimo_sinal_cor_css = 'var(--compra-borda)'
            ultimo_sinal_texto = f'✅ Última Entrada: COMPRA (Horário: {ultimo_sinal_hora})'
        elif ultimo_sinal_tipo == 'VENDA':
            ultimo_sinal_cor_css = 'var(--venda-borda)'
            ultimo_sinal_texto = f'❌ Última Entrada: VENDA (Horário: {ultimo_sinal_hora})'
        else:
            ultimo_sinal_cor_css = 'var(--neutro-borda)'
            ultimo_sinal_texto = '🟡 Nenhuma Entrada Forte Registrada'

        historico_html = formatar_historico_html(HISTORICO_SINAIS)
        assertividade_data = calcular_assertividade()

        html_content = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ROBÔ TRADER M1 - Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {{
    --bg-primary:#1C2331; --bg-secondary:#2A3346; --text-primary:#DCE3F4;
    --compra-borda:#6AA84F; --venda-borda:#E06666; --neutro-borda:#4D5970;
    --assert-fundo:#3B3F50; --assert-borda:#FFC107;
}}
body {{ background:#1C2331; color:#DCE3F4; font-family:'Poppins',sans-serif; padding:10px; }}
.container {{ max-width:950px; margin:20px auto; background:#2A3346; padding:20px; border-radius:20px; }}
h1 {{ text-align:center; color:#70A0FF; margin-bottom:25px; }}
.time-box {{ background:#3B3F50; padding:15px; border-radius:10px; text-align:center; margin-bottom:20px; }}
.current-time {{ font-size:2em; font-weight:700; }}
.last-signal-box {{ background:#3B3F50; border:1px solid #4D5970; border-left:5px solid {ultimo_sinal_cor_css}; padding:10px 15px; border-radius:8px; text-align:center; margin-bottom:20px; }}
.main-content-grid {{ display:flex; gap:15px; flex-direction:column; margin-bottom:25px; }}
@media(min-width:768px){{ .main-content-grid{{ flex-direction:row; }} }}
.sinal-box, .assertividade-box {{ flex:1; padding:20px; border-radius:15px; }}
.sinal-box {{ background:#374257; border:2px solid #4D5970; }}
.assertividade-box {{ background:#3B3F50; border:2px solid #FFC107; text-align:center; display:flex; flex-direction:column; justify-content:center; }}
.data-item {{ margin-bottom:8px; }}
pre {{ background:#101520; padding:15px; border-radius:12px; overflow:auto; color:#B0B0B0; }}
.win {{ color:#6AA84F; font-weight:700; }}
.loss {{ color:#E06666; font-weight:700; }}
</style>
</head>
<body>
<audio id="alertaAudio" src="{URL_ALERTE_SONORO}" preload="auto"></audio>
<div class="container">
<h1>ROBÔ TRADER M1 | DASHBOARD</h1>
<div class="time-box"><p>HORÁRIO ATUAL DE BRASÍLIA</p><div class="current-time">{horario_atual_brasilia}</div></div>
<div class="last-signal-box">{ultimo_sinal_texto}</div>
<div class="main-content-grid">
<div class="sinal-box">
<div class="data-item"><strong>SINAL:</strong> {sinal_exibicao}</div>
<div class="data-item"><strong>Ativo:</strong> {ULTIMO_SINAL['ativo']}</div>
<div class="data-item"><strong>Score:</strong> {ULTIMO_SINAL['score']}</div>
</div>
<div class="assertividade-box">
<p>Assertividade</p>
<p>{assertividade_data['percentual']}</p>
<p>Wins: {assertividade_data['wins']} / Total: {assertividade_data['total']}</p>
</div>
</div>
<h2>Histórico de Sinais</h2>
<pre>{historico_html or 'Nenhum registro ainda.'}</pre>
<h2>Gráfico de Velas</h2>
<div id="chart" style="height:350px;width:100%;"></div>
</div>
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
<script>
const chart = LightweightCharts.createChart(document.getElementById('chart'), {{
    width: document.getElementById('chart').offsetWidth,
    height: 350,
    layout: {{ backgroundColor:'#1C2331', textColor:'#DCE3F4' }},
    grid: {{ vertLines:{{color:'#2A3346'}}, horzLines:{{color:'#2A3346'}} }}
}});
const candleSeries = chart.addCandlestickSeries();
async function atualizarGrafico() {{
    try {{
        const resp = await fetch('/api/velas');
        const velas = await resp.json();
        if(velas.length>0){{
            candleSeries.setData(velas);
        }}
    }} catch(e){{ console.log('Erro gráfico:',e); }}
}}
atualizarGrafico();
setInterval(atualizarGrafico,5000);
document.addEventListener('click',function(){{ var audio = document.getElementById('alertaAudio'); if(audio){{ audio.volume=0.8; audio.play().catch(e=>console.log('Áudio bloqueado',e)); }} }});
</script>
</body>
</html>
"""
        return Response(html_content, mimetype='text/html')
    except Exception:
        print("Erro ao gerar dashboard:")
        traceback.print_exc()
        return Response("<h1>Erro ao gerar dashboard</h1><pre>"+traceback.format_exc()+"</pre>", mimetype='text/html')

# ====================== EXECUÇÃO ======================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
