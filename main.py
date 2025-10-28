# main.py
# ROBÔ TRADER M1 (WEB) - VERSÃO CORRIGIDA SEM GRÁFICO DE VELAS
from flask import Flask, Response
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
NUM_VELAS_ANALISE = 3
SCORE_MINIMO_SINAL = 2
MAX_HISTORICO = 10
DELTA_MIN = 0.0005  # 0.05% mínimo para considerar Win

# URL DO SOM DE ALERTA
URL_ALERTE_SONORO = "https://www.soundhelix.com/examples/audio/Wave-beep.wav"

# ====================== INICIALIZAÇÃO DO FLASK ======================
app = Flask(__name__)

# ====================== VARIÁVEIS GLOBAIS DE ESTADO ======================
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

ULTIMO_SINAL_REGISTRADO = {
    'horario': 'N/A',
    'sinal_tipo': 'N/A'
}

HISTORICO_SINAIS = []
ULTIMO_SINAL_CHECAR = None

# ====================== FUNÇÕES BASE ======================
def calcular_assertividade():
    if not HISTORICO_SINAIS:
        return {'total': 0, 'wins': 0, 'losses': 0, 'percentual': 'N/A'}
    wins = sum(1 for item in HISTORICO_SINAIS if item['resultado'] == 'WIN ✅')
    total = len(HISTORICO_SINAIS)
    losses = total - wins
    percentual = f"{(wins / total) * 100:.2f}%" if total else 'N/A'
    return {'total': total, 'wins': wins, 'losses': losses, 'percentual': percentual}

def get_ultimas_velas(ativo):
    try:
        params = {'symbol': ativo, 'type': INTERVALO}
        r = requests.get(API_BASE_URL, params=params, timeout=8)
        r.raise_for_status()
        data = r.json().get('data', [])
        velas = []
        for v in data[-(NUM_VELAS_ANALISE + 1):]:
            # v[1]=open, v[2]=low, v[3]=close, v[4]=high
            velas.append([float(v[1]), float(v[3]), float(v[4]), float(v[2])])
        return velas
    except Exception as e:
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Erro ao obter velas de {ativo}: {e}")
        return []

def analisar_price_action(velas):
    if len(velas) < 3:
        return {'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}

    score = 0
    for i in range(-3, 0):
        o, c = velas[i][0], velas[i][1]
        movimento = (c - o) / o
        if movimento > DELTA_MIN:
            score += 1
        elif movimento < -DELTA_MIN:
            score -= 1

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

    preco_entrada = velas[-1][1]
    return {'sinal': sinal_emoji, 'score': score, 'preco_entrada': preco_entrada}

def checar_resultado_sinal(sinal_checar):
    global HISTORICO_SINAIS
    try:
        ativo = sinal_checar['ativo']
        preco_entrada = sinal_checar['preco_entrada']
        direcao_sinal = sinal_checar['sinal']
        index_entrada = sinal_checar.get('index_entrada', -1)

        if ativo == 'N/A' or 'NEUTRO' in direcao_sinal:
            return

        velas = get_ultimas_velas(ativo)
        if len(velas) <= index_entrada:
            print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Sem vela seguinte para checar resultado de {ativo}.")
            return

        c_exp = velas[index_entrada + 1][1]  # fechamento da próxima vela

        resultado = 'NEUTRO'
        if 'COMPRA' in direcao_sinal:
            resultado = 'WIN ✅' if (c_exp - preco_entrada)/preco_entrada > DELTA_MIN else 'LOSS ❌'
        elif 'VENDA' in direcao_sinal:
            resultado = 'WIN ✅' if (preco_entrada - c_exp)/preco_entrada > DELTA_MIN else 'LOSS ❌'

        HISTORICO_SINAIS.append({
            'horario': sinal_checar['horario'],
            'ativo': ativo,
            'sinal': direcao_sinal,
            'resultado': resultado,
            'preco_entrada': preco_entrada,
            'preco_expiracao': c_exp
        })

        if len(HISTORICO_SINAIS) > MAX_HISTORICO:
            HISTORICO_SINAIS.pop(0)

        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] 🎯 Resultado de {ativo} ({sinal_checar['horario']}): {resultado}")
    except Exception:
        print("Erro em checar_resultado_sinal:")
        traceback.print_exc()

def formatar_historico_html(historico):
    linhas_html = []
    for item in reversed(historico):
        classe = 'win' if 'WIN' in item['resultado'] else 'loss'
        linha = (
            f"[{item['horario']}] {item['ativo']} -> "
            f"<span class='{classe}'>{item['resultado']}</span> "
            f"(Sinal: {item['sinal']})"
        )
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

            melhor = {'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0, 'index_entrada': -1}

            for ativo in ATIVOS_MONITORADOS:
                velas = get_ultimas_velas(ativo)
                analise = analisar_price_action(velas)
                if abs(analise['score']) >= abs(melhor['score']):
                    melhor = {'ativo': ativo, **analise, 'index_entrada': len(velas)-1}

            if abs(melhor['score']) >= SCORE_MINIMO_SINAL:
                sinal_final = copy.deepcopy(melhor)
                sinal_final['horario'] = horario_atual_str
                ULTIMO_SINAL_CHECAR = copy.deepcopy(sinal_final)
                ULTIMO_SINAL_REGISTRADO = {
                    'horario': sinal_final['horario'],
                    'sinal_tipo': 'COMPRA' if 'COMPRA' in sinal_final['sinal'] else 'VENDA'
                }
            else:
                sinal_final = {
                    'horario': horario_atual_str,
                    'ativo': 'N/A',
                    'sinal': 'NEUTRO 🟡',
                    'score': 0,
                    'preco_entrada': 0.0,
                    'index_entrada': -1
                }

            ULTIMO_SINAL.update({
                'horario': sinal_final['horario'],
                'ativo': sinal_final['ativo'],
                'sinal': sinal_final['sinal'],
                'score': sinal_final['score'],
                'preco_entrada': sinal_final['preco_entrada']
            })

            print(f"[{horario_atual_str}] 📢 Novo Sinal: {ULTIMO_SINAL['ativo']} - {ULTIMO_SINAL['sinal']} (Score: {ULTIMO_SINAL['score']})")
        except Exception:
            print("Erro no ciclo_analise:")
            traceback.print_exc()
        time.sleep(60)

# start background analysis thread
analysis_thread = Thread(target=ciclo_analise, daemon=True)
analysis_thread.start()

# ====================== ROTA DA DASHBOARD ======================
@app.route('/')
def home():
    try:
        assertividade_data = calcular_assertividade()
        horario_atual_brasilia = get_horario_brasilia().strftime('%H:%M:%S')

        sinal_exibicao = ULTIMO_SINAL['sinal']
        horario_exibicao = ULTIMO_SINAL['horario']

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

        signal_details_html = ""
        if ULTIMO_SINAL['score'] != 0:
            signal_details_html = f"""
                <div class="data-item">Horário do Sinal Ativo: <strong>{horario_exibicao}</strong></div>
                <div class="data-item">Preço de Entrada: <strong>{ULTIMO_SINAL['preco_entrada']:.5f}</strong></div>
                <div class="data-item">Força (Score): <strong>{ULTIMO_SINAL['score']}</strong></div>
            """

        historico_html = formatar_historico_html(HISTORICO_SINAIS)

        html_content = f'''<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>ROBÔ TRADER M1 - Dashboard</title>
<style>
:root {{
    --bg-primary: #1C2331;
    --bg-secondary: #2A3346;
    --text-primary: #DCE3F4;
    --accent-blue: #70A0FF;
    --neutro-fundo: #374257;
    --neutro-borda: #4D5970;
    --compra-borda: #6AA84F;
    --venda-borda: #E06666;
    --assert-borda: #FFC107;
}}

body {{
    background-color: var(--bg-primary);
    color: var(--text-primary);
    font-family: 'Poppins', sans-serif;
    padding: 10px;
    transition: background-color 0.5s;
}}
.container {{
    max-width: 950px;
    margin: 20px auto;
    background-color: var(--bg-secondary);
    padding: 20px;
    border-radius: 20px;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
}}
h1 {{ color: var(--accent-blue); border-bottom: 1px solid var(--neutro-borda); padding-bottom: 15px; margin-bottom: 25px; text-align: center; font-weight: 600; font-size: 1.8em; }}
.data-item {{ margin-bottom: 8px; font-size: 1.0em; font-weight: 400; }}
.data-item strong {{ font-weight: 600; color: #FFFFFF; }}
.win {{ color: var(--compra-borda); font-weight:700; }}
.loss {{ color: var(--venda-borda); font-weight:700; }}
</style>
</head>
<body>
<audio id="alertaAudio" src="{URL_ALERTE_SONORO}" preload="auto"></audio>
<div class="container">
    <h1>ROBÔ TRADER M1 | DASHBOARD SNIPER</h1>
    <div class="data-item">{ultimo_sinal_texto}</div>
    <div class="data-item">Sinal Atual: <strong>{sinal_exibicao}</strong></div>
    {signal_details_html}
    <h2>Histórico de Sinais</h2>
    <pre>{historico_html or 'Nenhum registro ainda.'}</pre>
    <div class="data-item">Assertividade: {assertividade_data['percentual']} | Wins: {assertividade_data['wins']} / Total: {assertividade_data['total']}</div>
</div>
<script>
document.addEventListener('click', function() {{
    var audio = document.getElementById('alertaAudio');
    if (audio) {{
        audio.volume = 0.8;
        audio.play().catch(function(e){{ console.log('Áudio bloqueado: ', e); }});
    }}
}});
</script>
</body>
</html>
'''
        return Response(html_content, mimetype='text/html')
    except Exception:
        print("Erro ao gerar dashboard:")
        traceback.print_exc()
        return Response("<h1>Erro ao gerar dashboard</h1><pre>" + traceback.format_exc() + "</pre>", mimetype='text/html')

# ====================== RODAR A APLICAÇÃO ======================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
