# main.py
# ROBÔ TRADER M1 (WEB) - VERSÃO CORRIGIDA
# Corrige erro de string tripla e mantém interface
# Aprimorado: Cores do Sinal Dinâmicas no Dashboard

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
SCORE_MINIMO_SINAL = 2.0
MAX_HISTORICO = 10

# URL DO SOM DE ALERTE
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
        # pega as últimas velas
        for v in data[-(NUM_VELAS_ANALISE + 1):]:
            # v[1] = open, v[3] = close, v[4] = high, v[2] = low
            velas.append([float(v[1]), float(v[3]), float(v[4]), float(v[2])])
        return velas
    except Exception as e:
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Erro ao obter velas de {ativo}: {e}")
        return []

def analisar_price_action(velas):
    if len(velas) < 2:
        return {'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}
    o1, c1 = velas[-1][0], velas[-1][3]
    o2, c2 = velas[-2][0], velas[-2][3]

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
        if ativo == 'N/A' or 'NEUTRO' in direcao_sinal:
            return
        velas = get_ultimas_velas(ativo)
        if len(velas) < 1:
            print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Sem dados para checar resultado de {ativo}.")
            return
        c_exp = velas[-1][3]
        resultado = 'NEUTRO'
        if 'COMPRA' in direcao_sinal:
            resultado = 'WIN ✅' if c_exp > preco_entrada else 'LOSS ❌'
        elif 'VENDA' in direcao_sinal:
            resultado = 'WIN ✅' if c_exp < preco_entrada else 'LOSS ❌'

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

            print(f"[{horario_atual_str}] Iniciando novo ciclo de análise...")
            melhor = {'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}

            for ativo in ATIVOS_MONITORADOS:
                velas = get_ultimas_velas(ativo)
                analise = analisar_price_action(velas)
                if abs(analise['score']) >= abs(melhor['score']):
                    melhor = {'ativo': ativo, **analise}

            if abs(melhor['score']) == SCORE_MINIMO_SINAL:
                sinal_final = melhor
                sinal_final['horario'] = horario_atual_str
            else:
                sinal_final = {
                    'horario': horario_atual_str,
                    'ativo': 'N/A',
                    'sinal': 'NEUTRO 🟡',
                    'score': 0,
                    'preco_entrada': 0.0
                }

            if abs(sinal_final['score']) == SCORE_MINIMO_SINAL:
                ULTIMO_SINAL_CHECAR = copy.deepcopy(sinal_final)
                ULTIMO_SINAL_REGISTRADO = {
                    'horario': sinal_final['horario'],
                    'sinal_tipo': 'COMPRA' if 'COMPRA' in sinal_final['sinal'] else 'VENDA'
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

# Inicia a thread de análise em segundo plano
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
        
        # --- Lógica de Cor e Animação ---
        sinal_cor_fundo = 'var(--neutro-fundo)'
        sinal_cor_borda = 'var(--neutro-borda)'
        sinal_classe_animacao = ''
        
        if 'COMPRA FORTE' in ULTIMO_SINAL['sinal']:
            sinal_cor_fundo = 'var(--compra-fundo)' 
            sinal_cor_borda = 'var(--compra-borda)' 
            sinal_classe_animacao = 'signal-active'
            explicacao = (
                f"Entrada de <strong>COMPRA FORTE</strong> no ativo <strong>{ULTIMO_SINAL['ativo']}</strong>."
                f"<br>Estratégia: O preço demonstrou força de alta por <strong>duas ou mais velas M1 consecutivas</strong> (Score: {ULTIMO_SINAL['score']})."
            )
        elif 'VENDA FORTE' in ULTIMO_SINAL['sinal']:
            sinal_cor_fundo = 'var(--venda-fundo)' 
            sinal_cor_borda = 'var(--venda-borda)' 
            sinal_classe_animacao = 'signal-active'
            explicacao = (
                f"Entrada de <strong>VENDA FORTE</strong> no ativo <strong>{ULTIMO_SINAL['ativo']}</strong>."
                f"<br>Estratégia: O preço demonstrou força de baixa por <strong>duas ou mais velas M1 consecutivas</strong> (Score: {ULTIMO_SINAL['score']})."
            )
        else:
            sinal_exibicao = 'SEM SINAL DE ENTRADA'
            explicacao = (
                "No momento, o robô está em <strong>NEUTRO</strong>. Nenhuma moeda atingiu score 2 ou -2."
                "<br>Estratégia: Aguardando a formação de <strong>duas ou mais velas M1 consecutivas</strong> na mesma direção forte."
            )
        # --- Fim Lógica de Cor e Animação ---

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

        # Prepara detalhes do sinal e histórico
        if ULTIMO_SINAL['score'] != 0:
            signal_details_html = f"""
                <div class="data-item">Horário do Sinal Ativo: <strong>{horario_exibicao}</strong></div>
                <div class="data-item">Preço de Entrada: <strong>{ULTIMO_SINAL['preco_entrada']:.5f}</strong></div>
                <div class="data-item">Força (Score): <strong>{ULTIMO_SINAL['score']}</strong></div>
            """
            analise_detail_html = ""
        else:
            signal_details_html = ""
            analise_detail_html = f"""
                <div class="data-item">Última Análise do Robô: <strong>{horario_exibicao}</strong></div>
            """

        historico_html = formatar_historico_html(HISTORICO_SINAIS)

        # Usando ''' para o CSS para evitar conflitos de aspas
        # Variáveis dinâmicas para o CSS são injetadas aqui
        css_content = f'''
        @keyframes pulse {{
            0% {{ box-shadow: 0 0 0 0 rgba(112, 160, 255, 0.7); }}
            70% {{ box-shadow: 0 0 0 15px rgba(112, 160, 255, 0); }}
            100% {{ box-shadow: 0 0 0 0 rgba(112, 160, 255, 0); }}
        }}

        /* Paleta de Cores e Estilos */
        :root {{
            --bg-primary: #1C2331;
            --bg-secondary: #2A3346;
            --text-primary: #DCE3F4;
            --accent-blue: #70A0FF;
            --neutro-fundo: #374257;
            --neutro-borda: #4D5970;
            --compra-fundo: #2D4C42; /* Verde Trade Escuro */
            --compra-borda: #6AA84F; /* Verde Trade */
            --venda-fundo: #5C3A3A; /* Vermelho Trade Escuro */
            --venda-borda: #E06666; /* Vermelho Trade */
            --assert-fundo: #3B3F50;
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
        .time-box {{ background-color: #3B3F50; padding: 15px; border-radius: 10px; text-align: center; margin-bottom: 20px; box-shadow: 0 3px 10px rgba(0, 0, 0, 0.4); }}
        .current-time {{ font-size: 2.0em; font-weight: 700; color: #FFFFFF; line-height: 1.1; }}
        .last-signal-box {{ background-color: #3B3F50; border: 1px solid #4D5970; border-left: 5px solid {ultimo_sinal_cor_css}; padding: 10px 15px; border-radius: 8px; margin-bottom: 20px; font-size: 1.0em; font-weight: 500; color: var(--text-primary); text-align: center; box-shadow: 0 3px 10px rgba(0, 0, 0, 0.4); }}
        .main-content-grid {{ display: flex; gap: 15px; margin-bottom: 25px; flex-direction: column; }}
        @media (min-width: 768px) {{ .main-content-grid {{ flex-direction: row; }} }}
        
        /* CORES DINÂMICAS DO SINAL */
        .sinal-box {{ 
            flex: 1; padding: 20px; border-radius: 15px; transition: all 0.5s ease-in-out; box-shadow: 0 5px 15px rgba(0,0,0,0.3);
            background-color: {sinal_cor_fundo}; 
            border: 2px solid {sinal_cor_borda}; 
        }}
        .sinal-header {{ 
            font-size: 1.8em; font-weight: 700; margin-bottom: 10px; 
            color: {sinal_cor_borda}; /* Cor do texto no header é a mesma da borda */
        }}

        .data-item {{ margin-bottom: 8px; font-size: 1.0em; font-weight: 400; }}
        .data-item strong {{ font-weight: 600; color: #FFFFFF; }}
        
        /* ANIMAÇÃO DE ALERTA */
        .signal-active {{ 
            animation: pulse 1s infinite;
            box-shadow: 0 0 20px {sinal_cor_borda};
            transform: translateY(-2px);
        }}

        .assertividade-box {{ 
            background-color: var(--assert-fundo); border: 2px solid var(--assert-borda); text-align: center; display:flex; flex-direction: column; justify-content:center; 
        }}
        .assertividade-box span {{ font-weight: 700; color: var(--assert-borda); font-size: 2.5em; line-height: 1.1; margin: 5px 0; }}

        h2 {{ color: var(--accent-blue); font-weight: 600; margin-bottom: 10px; font-size: 1.5em; }}
        pre {{ background-color: #101520; padding: 15px; border-radius: 12px; overflow:auto; color: #B0B0B0; font-size: 0.85em; }}
        .win {{ color: var(--compra-borda); font-weight:700; }}
        .loss {{ color: var(--venda-borda); font-weight:700; }}
        .warning-message {{ background-color: #FFC10720; color:#FFC107; padding:8px; border-radius:8px; text-align:center; margin-bottom:15px; font-weight:500; border:1px solid #FFC107; font-size:0.9em; }}
        .info-box {{ margin-top:25px; padding:15px; background-color:#30394c; border-left:5px solid var(--accent-blue); border-radius:8px; font-size:0.95em; line-height:1.6; color:#B0B9CC; }}
        '''

        # Monta o HTML (usar f-string segura)
        html_content = f'''<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>ROBÔ TRADER M1 - Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
{css_content}
</style>
</head>
<body>
<audio id="alertaAudio" src="{URL_ALERTE_SONORO}" preload="auto"></audio>
<div class="container">
    <h1>ROBÔ TRADER M1 | DASHBOARD SNIPER</h1>

    <div class="time-box">
        <p style="margin-bottom:0;">HORÁRIO ATUAL DE BRASÍLIA</p>
        <div class="current-time">{horario_atual_brasilia}</div>
    </div>

    <div class="warning-message">
        ⚠️ Aviso: O apito de entrada está configurado, mas o navegador pode bloqueá-lo. Clique na tela para liberar o som.
    </div>

    <div class="last-signal-box">{ultimo_sinal_texto}</div>

    <div class="main-content-grid">
        <div class="sinal-box {sinal_classe_animacao}">
            <div class="sinal-header">SINAL ATUAL</div>
            <div class="data-item">Sinal: <strong>{sinal_exibicao}</strong></div>
            <div class="data-item">Ativo: <strong>{ULTIMO_SINAL['ativo']}</strong></div>
            {signal_details_html}
            {analise_detail_html}
        </div>

        <div class="assertividade-box">
            <p>Assertividade</p>
            <span>{assertividade_data['percentual']}</span>
            <p style="margin-top:8px;">Wins: {assertividade_data['wins']} / Total: {assertividade_data['total']}</p>
        </div>
    </div>

    <h2>Histórico de Sinais</h2>
    <pre>{historico_html or 'Nenhum registro ainda.'}</pre>

    <div class="info-box">
        <strong>Explicação:</strong>
        <div style="margin-top:8px;">{explicacao}</div>
    </div>
</div>

<script>
// Lógica de áudio para tocar o bipe em caso de sinal FORTE
function checkSignalAndPlayAudio() {{
    const signal = "{sinal_exibicao}";
    const audio = document.getElementById('alertaAudio');
    
    // Toca o áudio apenas se for um sinal FORTE e se o áudio estiver carregado
    if (signal.includes('FORTE') && audio) {{
        audio.currentTime = 0; 
        audio.volume = 0.8; 
        audio.play().catch(function(e){{ console.log('Áudio bloqueado pelo navegador.', e); }});
    }}
}}

// Tenta tocar o áudio imediatamente se houver sinal forte (pode falhar devido a políticas de autoplay)
checkSignalAndPlayAudio();

// Adiciona um evento de clique para desbloquear o áudio, caso o navegador o tenha bloqueado
document.addEventListener('click', function() {{
    var audio = document.getElementById('alertaAudio');
    if (audio && audio.paused) {{
        audio.volume = 0; // Toca silenciosamente para desbloquear
        audio.play().catch(function(e){{ console.log('Áudio desbloqueado em clique.', e); }});
        audio.volume = 0.8; // Volta ao volume normal
    }}
}}, {{ once: true }}); // Executa apenas uma vez para desbloquear
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
    # Porta para execução em nuvem (Render/Replit) ou local
    port = int(os.environ.get('PORT', 5000))
    # Usar 0.0.0.0 para acessar externamente (containers)
    app.run(host='0.0.0.0', port=port, debug=True)
