# main.py
# ROBÔ TRADER M1 (WEB) - VERSÃO SSE (Sem Flicker)
# Dashboard agora usa Server-Sent Events (SSE) para atualização suave.

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
INTERVALO = '1min'
NUM_VELAS_ANALISE = 3
SCORE_MINIMO_SINAL = 2.0
MAX_HISTORICO = 10
# Intervalo de atualização do dashboard via SSE
DASHBOARD_REFRESH_RATE_SECONDS = 5 

# URL DO SOM DE ALERTE
URL_ALERTE_SONORO = "https://www.soundhelix.com/examples/audio/Wave-beep.wav"

# ====================== INICIALIZAÇÃO DO FLASK ======================
app = Flask(__name__)

# Lock para garantir acesso seguro às variáveis globais
state_lock = Lock()

# ====================== VARIÁVEIS GLOBAIS DE ESTADO ======================
def get_horario_brasilia():
    fuso_brasil = pytz.timezone(TIMEZONE_BR)
    return datetime.now(fuso_brasil)

# O estado global ULTIMO_SINAL será armazenado de forma protegida
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
    with state_lock:
        if not HISTORICO_SINAIS:
            return {'total': 0, 'wins': 0, 'losses': 0, 'percentual': 'N/A'}

        wins = sum(1 for item in HISTORICO_SINAIS if item['resultado'] == 'WIN ✅')
        total = len(HISTORICO_SINAIS)
        losses = total - wins
        percentual = f"{(wins / total) * 100:.2f}%" if total else 'N/A'
        return {'total': total, 'wins': wins, 'losses': losses, 'percentual': percentual}

def get_ultimas_velas(ativo):
    # Aprimorado: Implementar Exponential Backoff aqui seria ideal para resiliência
    try:
        params = {'symbol': ativo, 'type': INTERVALO}
        r = requests.get(API_BASE_URL, params=params, timeout=8)
        r.raise_for_status()
        data = r.json().get('data', [])

        velas = []
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
    
    # Ultima vela (a que está fechando) e Penúltima (fechada)
    o1, c1 = velas[-1][0], velas[-1][3]
    o2, c2 = velas[-2][0], velas[-2][3]

    score = 0
    # Regra: Se vela atual (c1 > o1) é verde (+1), se vermelha (-1)
    if c1 > o1: score += 1
    elif c1 < o1: score -= 1
    # Regra: Se vela anterior (c2 > o2) é verde (+1), se vermelha (-1)
    if c2 > o2: score += 1
    elif c2 < o2: score -= 1

    # ... (aqui entraria a lógica de SL/TP se fosse implementada)

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
        
        # Preço de Fechamento da Vela de Expiração
        c_exp = velas[-1][3] 
        resultado = 'NEUTRO'

        if 'COMPRA' in direcao_sinal:
            # WIN se o fechamento for MAIOR que o preço de entrada
            resultado = 'WIN ✅' if c_exp > preco_entrada else 'LOSS ❌'
        elif 'VENDA' in direcao_sinal:
            # WIN se o fechamento for MENOR que o preço de entrada
            resultado = 'WIN ✅' if c_exp < preco_entrada else 'LOSS ❌'

        with state_lock:
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
        # Adiciona a diferença de preço para detalhe
        diferenca = item['preco_expiracao'] - item['preco_entrada']
        sinal_diff = "+" if diferenca >= 0 else ""
        
        linha = (
            f"[{item['horario']}] {item['ativo']} -> "
            f"<span class='{classe}'>{item['resultado']}</span> "
            f"(Sinal: {item['sinal']}. {sinal_diff}{diferenca:.5f})"
        )
        linhas_html.append(linha)
    return '\n'.join(linhas_html)

# ====================== CICLO DE ANÁLISE (BACKGROUND) ======================
def ciclo_analise():
    global ULTIMO_SINAL, ULTIMO_SINAL_CHECAR, ULTIMO_SINAL_REGISTRADO
    # Primeira iteração espera 1 segundo para o setup inicial
    time.sleep(1) 
    
    while True:
        try:
            # --- Melhoria 1: Agendamento Alinhado ao Minuto ---
            now_dt = get_horario_brasilia()
            seconds_until_next_minute = 60 - now_dt.second
            # Se for 00 segundos, esperamos 60s. Caso contrário, esperamos o restante.
            sleep_time = seconds_until_next_minute if seconds_until_next_minute != 60 else 60
            
            # Garante que a checagem do resultado só ocorra se a próxima vela fechou.
            if ULTIMO_SINAL_CHECAR:
                checar_resultado_sinal(ULTIMO_SINAL_CHECAR)
                # Reseta o sinal para checar
                with state_lock:
                    ULTIMO_SINAL_CHECAR = None

            now_dt = get_horario_brasilia()
            horario_atual_str = now_dt.strftime('%H:%M:%S')

            print(f"[{horario_atual_str}] Iniciando novo ciclo de análise...")
            melhor = {'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}

            for ativo in ATIVOS_MONITORADOS:
                velas = get_ultimas_velas(ativo)
                analise = analisar_price_action(velas)
                if abs(analise['score']) >= abs(melhor['score']):
                    melhor = {'ativo': ativo, **analise}

            sinal_final = {
                'horario': horario_atual_str,
                'ativo': melhor['ativo'],
                'sinal': melhor['sinal'],
                'score': melhor['score'],
                'preco_entrada': melhor['preco_entrada']
            }
            
            with state_lock:
                # Se encontrou um sinal forte, registra para checar o resultado na próxima iteração
                if abs(sinal_final['score']) >= SCORE_MINIMO_SINAL:
                    ULTIMO_SINAL_CHECAR = copy.deepcopy(sinal_final)
                    ULTIMO_SINAL_REGISTRADO.update({
                        'horario': sinal_final['horario'],
                        'sinal_tipo': 'COMPRA' if 'COMPRA' in sinal_final['sinal'] else 'VENDA'
                    })

                # Atualiza o ULTIMO_SINAL para ser exibido no dashboard
                ULTIMO_SINAL.update(sinal_final)

            print(f"[{horario_atual_str}] 📢 Novo Sinal: {ULTIMO_SINAL['ativo']} - {ULTIMO_SINAL['sinal']} (Score: {ULTIMO_SINAL['score']})")

        except Exception:
            print("Erro no ciclo_analise:")
            traceback.print_exc()
            sleep_time = 10 # Se houver erro, espera 10s e tenta novamente

        # Pausa até o próximo fechamento de vela
        time.sleep(sleep_time)

# Inicia a thread de análise em segundo plano
analysis_thread = Thread(target=ciclo_analise, daemon=True)
analysis_thread.start()

# ====================== GERAÇÃO DINÂMICA DO CONTEÚDO (Para SSE) ======================
def render_dashboard_content():
    # Esta função agora retorna APENAS o HTML DENTRO do body que precisa ser atualizado.
    with state_lock:
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

        # Usando f-string para o CSS. Isso será usado pelo JS para aplicar estilos dinâmicos
        dynamic_style_css = f"""
            .last-signal-box {{ border-left: 5px solid {ultimo_sinal_cor_css}; }}
            .sinal-box {{ 
                background-color: {sinal_cor_fundo}; 
                border: 2px solid {sinal_cor_borda}; 
            }}
            .sinal-header {{ 
                color: {sinal_cor_borda};
            }}
            .signal-active {{ 
                animation: pulse 1s infinite;
                box-shadow: 0 0 20px {sinal_cor_borda};
                transform: translateY(-2px);
            }}
        """

        # Retorna um JSON com todos os dados para o JS atualizar o DOM
        data_payload = {
            'time': horario_atual_brasilia,
            'ultimoSinalTexto': ultimo_sinal_texto,
            'sinalExibicao': sinal_exibicao,
            'ativo': ULTIMO_SINAL['ativo'],
            'signalDetails': signal_details_html,
            'analiseDetail': analise_detail_html,
            'assertPercentual': assertividade_data['percentual'],
            'assertWins': assertividade_data['wins'],
            'assertTotal': assertividade_data['total'],
            'historicoHtml': historico_html or 'Nenhum registro ainda.',
            'explicacaoHtml': explicacao,
            'sinalClasseAnimacao': sinal_classe_animacao,
            'isSinalForte': 'FORTE' in sinal_exibicao,
            'dynamicCss': dynamic_style_css
        }
        return data_payload

# ====================== ROTA DE STREAM SSE (MELHORIA UX) ======================
@app.route('/stream')
def stream():
    def event_stream():
        while True:
            try:
                # 1. Gera o JSON de dados
                data = render_dashboard_content()
                
                # 2. Formata como Server-Sent Event (SSE)
                # O SSE precisa do formato 'data: <json>\n\n'
                yield f"data: {json.dumps(data)}\n\n"
                
                # 3. Pausa para o próximo envio (sem refresh de página!)
                time.sleep(DASHBOARD_REFRESH_RATE_SECONDS)
            except Exception:
                print("Erro no stream SSE:")
                traceback.print_exc()
                time.sleep(5)

    return Response(
        event_stream(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )

# ====================== ROTA DA DASHBOARD (HTML Estático + JS do SSE) ======================
@app.route('/')
def home():
    # --- CSS Estático (não muda por SSE) ---
    css_content = '''
    /* Paleta de Cores e Estilos */
    :root {
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
    }

    body {
        background-color: var(--bg-primary);
        color: var(--text-primary);
        font-family: 'Poppins', sans-serif;
        padding: 10px;
    }
    .container {
        max-width: 950px;
        margin: 20px auto;
        background-color: var(--bg-secondary);
        padding: 20px;
        border-radius: 20px;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
    }
    h1 { color: var(--accent-blue); border-bottom: 1px solid var(--neutro-borda); padding-bottom: 15px; margin-bottom: 25px; text-align: center; font-weight: 600; font-size: 1.8em; }
    .time-box { background-color: #3B3F50; padding: 15px; border-radius: 10px; text-align: center; margin-bottom: 20px; box-shadow: 0 3px 10px rgba(0, 0, 0, 0.4); }
    .current-time { font-size: 2.0em; font-weight: 700; color: #FFFFFF; line-height: 1.1; }
    .last-signal-box { 
        background-color: #3B3F50; border: 1px solid #4D5970; border-left: 5px solid var(--neutro-borda); 
        padding: 10px 15px; border-radius: 8px; margin-bottom: 20px; font-size: 1.0em; font-weight: 500; 
        color: var(--text-primary); text-align: center; box-shadow: 0 3px 10px rgba(0, 0, 0, 0.4); 
        transition: border-color 0.5s ease;
    }
    .main-content-grid { display: flex; gap: 15px; margin-bottom: 25px; flex-direction: column; }
    @media (min-width: 768px) { .main-content-grid { flex-direction: row; } }
    
    .sinal-box { 
        flex: 1; padding: 20px; border-radius: 15px; transition: all 0.5s ease-in-out; box-shadow: 0 5px 15px rgba(0,0,0,0.3);
        /* Cores iniciais */
        background-color: var(--neutro-fundo); 
        border: 2px solid var(--neutro-borda); 
    }
    .sinal-header { 
        font-size: 1.8em; font-weight: 700; margin-bottom: 10px; 
        color: var(--neutro-borda);
        transition: color 0.5s ease; 
    }

    .data-item { margin-bottom: 8px; font-size: 1.0em; font-weight: 400; }
    .data-item strong { font-weight: 600; color: #FFFFFF; }
    
    /* ANIMAÇÃO DE ALERTA */
    @keyframes pulse {
        0% { box-shadow: 0 0 0 0 rgba(112, 160, 255, 0.7); }
        70% { box-shadow: 0 0 0 15px rgba(112, 160, 255, 0); }
        100% { box-shadow: 0 0 0 0 rgba(112, 160, 255, 0); }
    }
    /* Classe .signal-active é aplicada dinamicamente via JS */
    
    .assertividade-box { 
        background-color: var(--assert-fundo); border: 2px solid var(--assert-borda); text-align: center; display:flex; flex-direction: column; justify-content:center; 
    }
    .assertividade-box span { font-weight: 700; color: var(--assert-borda); font-size: 2.5em; line-height: 1.1; margin: 5px 0; }

    h2 { color: var(--accent-blue); font-weight: 600; margin-bottom: 10px; font-size: 1.5em; }
    pre { background-color: #101520; padding: 15px; border-radius: 12px; overflow:auto; color: #B0B0B0; font-size: 0.85em; }
    .win { color: var(--compra-borda); font-weight:700; }
    .loss { color: var(--venda-borda); font-weight:700; }
    .warning-message { background-color: #FFC10720; color:#FFC107; padding:8px; border-radius:8px; text-align:center; margin-bottom:15px; font-weight:500; border:1px solid #FFC107; font-size:0.9em; }
    .info-box { margin-top:25px; padding:15px; background-color:#30394c; border-left:5px solid var(--accent-blue); border-radius:8px; font-size:0.95em; line-height:1.6; color:#B0B9CC; }
    '''

    # Monta o HTML (Sem meta refresh!)
    html_content = f'''<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ROBÔ TRADER M1 - Dashboard SSE</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style id="dynamic-css">
{css_content}
</style>
</head>
<body>
<audio id="alertaAudio" src="{URL_ALERTE_SONORO}" preload="auto"></audio>
<div class="container">
    <h1>ROBÔ TRADER M1 | DASHBOARD SNIPER</h1>

    <div class="time-box">
        <p style="margin-bottom:0;">HORÁRIO ATUAL DE BRASÍLIA</p>
        <div class="current-time" id="current-time">--:--:--</div>
    </div>

    <div class="warning-message">
        ⚠️ Aviso: O apito de entrada está configurado. Clique na tela para liberar o áudio. O dashboard usa **atualização em tempo real (SSE)**.
    </div>

    <div class="last-signal-box" i
