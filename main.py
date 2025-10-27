# main.py
# ROBÔ TRADER M1 (WEB) - VERSÃO SL/TP SIMULADO
# Adicionada a simulação de Stop Loss (SL) e Take Profit (TP) de 0.05%
# para um backtesting de resultados mais realista.

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
INTERVALO_M5 = '5min' 
NUM_VELAS_ANALISE = 3
SCORE_MINIMO_SINAL = 2.0
MAX_HISTORICO = 10
# Novo: Stop Loss e Take Profit definidos em 0.05%
PERCENTUAL_SL_TP = 0.0005 
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

# ====================== FUNÇÕES BASE E DE DADOS ======================

def calcular_assertividade():
    with state_lock:
        if not HISTORICO_SINAIS:
            return {'total': 0, 'wins': 0, 'losses': 0, 'percentual': 'N/A'}

        # Conta WINS e LOSSES, incluindo (TP) e (SL)
        wins = sum(1 for item in HISTORICO_SINAIS if 'WIN' in item['resultado'])
        total = len(HISTORICO_SINAIS)
        losses = total - wins
        percentual = f"{(wins / total) * 100:.2f}%" if total else 'N/A'
        return {'total': total, 'wins': wins, 'losses': losses, 'percentual': percentual}

def get_velas_kucoin(ativo, intervalo):
    """Busca as velas da KuCoin para um dado ativo e intervalo.
       Retorna no formato padronizado: [Open, Close, High, Low]
    """
    try:
        params = {'symbol': ativo, 'type': intervalo}
        r = requests.get(API_BASE_URL, params=params, timeout=8)
        r.raise_for_status()
        data = r.json().get('data', [])

        velas = []
        # KuCoin API: [timestamp, open, close, high, low, volume, turnover]
        # Nosso formato interno: [Open, Close, High, Low]
        for v in data:
            # Indices KuCoin: v[1]=open, v[2]=close, v[3]=high, v[4]=low
            velas.append([float(v[1]), float(v[2]), float(v[3]), float(v[4])])
        
        if intervalo == INTERVALO_M1:
            return velas[-(NUM_VELAS_ANALISE + 1):]
        elif intervalo == INTERVALO_M5:
            return velas[-2:] # Última fechada e a atual
            
    except Exception as e:
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Erro ao obter velas {intervalo} de {ativo}: {e}")
        return []
    return []

def get_tendencia_m5(ativo):
    """Determina a tendência com base no fechamento da última vela M5."""
    velas_m5 = get_velas_kucoin(ativo, INTERVALO_M5)
    
    if len(velas_m5) < 2:
        return 'NEUTRO' 

    # A vela que acabou de fechar (fechou no final do ciclo M5)
    # Formato: [Open, Close, High, Low]
    o_m5, c_m5 = velas_m5[-2][0], velas_m5[-2][1]
    
    if c_m5 > o_m5:
        return 'UP' 
    elif c_m5 < o_m5:
        return 'DOWN' 
    else:
        return 'NEUTRO' 

def analisar_price_action(velas_m1):
    """Gera o sinal M1 baseado na força das últimas velas M1."""
    if len(velas_m1) < 2:
        return {'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}
    
    # Formato interno: [Open, Close, High, Low]
    o1, c1 = velas_m1[-1][0], velas_m1[-1][1]
    o2, c2 = velas_m1[-2][0], velas_m1[-2][1]

    score = 0
    # Regra: Se vela atual (c1 > o1) é verde (+1), se vermelha (-1)
    if c1 > o1: score += 1
    elif c1 < o1: score -= 1
    # Regra: Se vela anterior (c2 > o2) é verde (+1), se vermelha (-1)
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
    """
    Novo: Simula o resultado considerando SL/TP de 0.05% com base nos High/Low da vela de expiração.
    """
    global HISTORICO_SINAIS
    try:
        ativo = sinal_checar['ativo']
        preco_entrada = sinal_checar['preco_entrada']
        direcao_sinal = sinal_checar['sinal']
        if ativo == 'N/A' or 'NEUTRO' in direcao_sinal or 'Filtrado' in direcao_sinal:
            return
        
        velas_exp = get_velas_kucoin(ativo, INTERVALO_M1)
        if len(velas_exp) < 1:
            print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Sem dados para checar resultado de {ativo}.")
            return
        
        # Dados da Vela de Expiração: [Open, Close, High, Low]
        o_exp, c_exp, h_exp, l_exp = velas_exp[-1] 
        resultado = 'NEUTRO'
        
        percentual_sl_tp = PERCENTUAL_SL_TP

        # ====================== Lógica de Checagem SL/TP ======================
        if 'COMPRA' in direcao_sinal: # Tenta atingir o TP (acima)
            tp_price = preco_entrada * (1 + percentual_sl_tp)
            sl_price = preco_entrada * (1 - percentual_sl_tp)
            
            # Prioridade 1: O TP foi atingido? (High da vela de expiração)
            if h_exp >= tp_price:
                resultado = 'WIN ✅ (TP)'
            # Prioridade 2: O SL foi atingido? (Low da vela de expiração)
            elif l_exp <= sl_price:
                resultado = 'LOSS ❌ (SL)'
            # Prioridade 3: Determinação pelo Fechamento (Se o SL/TP não foi atingido)
            else:
                resultado = 'WIN ✅ (Close)' if c_exp > preco_entrada else 'LOSS ❌ (Close)'
                
        elif 'VENDA' in direcao_sinal: # Tenta atingir o TP (abaixo)
            tp_price = preco_entrada * (1 - percentual_sl_tp)
            sl_price = preco_entrada * (1 + percentual_sl_tp)

            # Prioridade 1: O TP foi atingido? (Low da vela de expiração)
            if l_exp <= tp_price:
                resultado = 'WIN ✅ (TP)'
            # Prioridade 2: O SL foi atingido? (High da vela de expiração)
            elif h_exp >= sl_price:
                resultado = 'LOSS ❌ (SL)'
            # Prioridade 3: Determinação pelo Fechamento
            else:
                resultado = 'WIN ✅ (Close)' if c_exp < preco_entrada else 'LOSS ❌ (Close)'
        # ======================================================================

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
        diferenca = item['preco_expiracao'] - item['preco_entrada']
        sinal_diff = "+" if diferenca >= 0 else ""
        
        # Adiciona os detalhes do SL/TP ao histórico
        resultado_formatado = item['resultado'].replace(' (Close)', '')

        linha = (
            f"[{item['horario']}] {item['ativo']} -> "
            f"<span class='{classe}'>{resultado_formatado}</span> "
            f"(Sinal: {item['sinal']}. Fechamento Diff: {sinal_diff}{diferenca:.5f})"
        )
        linhas_html.append(linha)
    return '\n'.join(linhas_html)

# ====================== CICLO DE ANÁLISE (BACKGROUND) ======================
def ciclo_analise():
    global ULTIMO_SINAL, ULTIMO_SINAL_CHECAR, ULTIMO_SINAL_REGISTRADO
    time.sleep(1) 
    
    while True:
        try:
            # --- AGENDAMENTO: Alinhado ao Minuto (XX:XX:00) ---
            now_dt = get_horario_brasilia()
            seconds_until_next_minute = 60 - now_dt.second
            sleep_time = seconds_until_next_minute if seconds_until_next_minute != 60 else 60
            
            # Checa o resultado do sinal anterior
            if ULTIMO_SINAL_CHECAR:
                checar_resultado_sinal(ULTIMO_SINAL_CHECAR)
                with state_lock:
                    ULTIMO_SINAL_CHECAR = None

            now_dt = get_horario_brasilia()
            horario_atual_str = now_dt.strftime('%H:%M:%S')

            print(f"[{horario_atual_str}] Iniciando novo ciclo de análise...")
            melhor = {'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}

            for ativo in ATIVOS_MONITORADOS:
                velas_m1 = get_velas_kucoin(ativo, INTERVALO_M1)
                analise_m1 = analisar_price_action(velas_m1)
                
                # --- FILTRO M5 ---
                if 'FORTE' in analise_m1['sinal']:
                    tendencia_m5 = get_tendencia_m5(ativo)
                    
                    if tendencia_m5 == 'UP' and 'VENDA' in analise_m1['sinal']:
                        analise_m1['sinal'] = 'NEUTRO (Filtrado M5)'
                    
                    elif tendencia_m5 == 'DOWN' and 'COMPRA' in analise_m1['sinal']:
                        analise_m1['sinal'] = 'NEUTRO (Filtrado M5)'
                # --- FIM FILTRO M5 ---

                if abs(analise_m1['score']) >= abs(melhor['score']):
                    melhor = {'ativo': ativo, **analise_m1}

            sinal_final = {
                'horario': horario_atual_str,
                'ativo': melhor['ativo'],
                'sinal': melhor['sinal'],
                'score': melhor['score'],
                'preco_entrada': melhor['preco_entrada']
            }
            
            with state_lock:
                if abs(sinal_final['score']) >= SCORE_MINIMO_SINAL and 'Filtrado' not in sinal_final['sinal']:
                    ULTIMO_SINAL_CHECAR = copy.deepcopy(sinal_final)
                    ULTIMO_SINAL_REGISTRADO.update({
                        'horario': sinal_final['horario'],
                        'sinal_tipo': 'COMPRA' if 'COMPRA' in sinal_final['sinal'] else 'VENDA'
                    })

                ULTIMO_SINAL.update(sinal_final)

            print(f"[{horario_atual_str}] 📢 Novo Sinal: {ULTIMO_SINAL['ativo']} - {ULTIMO_SINAL['sinal']} (Score: {ULTIMO_SINAL['score']})")

        except Exception:
            print("Erro no ciclo_analise:")
            traceback.print_exc()
            sleep_time = 10 

        time.sleep(sleep_time)

# Inicia a thread de análise em segundo plano
analysis_thread = Thread(target=ciclo_analise, daemon=True)
analysis_thread.start()

# ====================== GERAÇÃO DINÂMICA DO CONTEÚDO (Para SSE) ======================
def render_dashboard_content():
    with state_lock:
        assertividade_data = calcular_assertividade()
        horario_atual_brasilia = get_horario_brasilia().strftime('%H:%M:%S')

        sinal_exibicao = ULTIMO_SINAL['sinal']
        horario_exibicao = ULTIMO_SINAL['horario']
        
        # --- Lógica de Cor e Animação ---
        sinal_cor_fundo = 'var(--neutro-fundo)'
        sinal_cor_borda = 'var(--neutro-borda)'
        sinal_classe_animacao = ''
        
        is_sinal_forte = 'FORTE' in ULTIMO_SINAL['sinal'] and 'Filtrado' not in ULTIMO_SINAL['sinal']

        if 'COMPRA FORTE' in ULTIMO_SINAL['sinal'] and is_sinal_forte:
            sinal_cor_fundo = 'var(--compra-fundo)' 
            sinal_cor_borda = 'var(--compra-borda)' 
            sinal_classe_animacao = 'signal-active'
            explicacao = (
                f"Entrada de <strong>COMPRA FORTE</strong> no ativo <strong>{ULTIMO_SINAL['ativo']}</strong>."
                f"<br><strong>Filtro M5: Confirmado.</strong> A tendência macro está de alta."
                f"<br><strong>Simulação SL/TP:</strong> Margem de {PERCENTUAL_SL_TP * 100:.2f}% (SL/TP) aplicada na checagem."
            )
        elif 'VENDA FORTE' in ULTIMO_SINAL['sinal'] and is_sinal_forte:
            sinal_cor_fundo = 'var(--venda-fundo)' 
            sinal_cor_borda = 'var(--venda-borda)' 
            sinal_classe_animacao = 'signal-active'
            explicacao = (
                f"Entrada de <strong>VENDA FORTE</strong> no ativo <strong>{ULTIMO_SINAL['ativo']}</strong>."
                f"<br><strong>Filtro M5: Confirmado.</strong> A tendência macro está de baixa."
                f"<br><strong>Simulação SL/TP:</strong> Margem de {PERCENTUAL_SL_TP * 100:.2f}% (SL/TP) aplicada na checagem."
            )
        else:
            if 'Filtrado' in ULTIMO_SINAL['sinal']:
                 explicacao = (
                    f"Sinal de {ULTIMO_SINAL['sinal']} detectado em <strong>{ULTIMO_SINAL['ativo']}</strong>, mas foi **REJEITADO** pelo Filtro M5."
                    "<br>Regra: O sinal M1 estava contra a tendência dominante do M5."
                )
            else:
                explicacao = (
                    "No momento, o robô está em <strong>NEUTRO</strong>. Nenhuma moeda atingiu score mínimo."
                    f"<br>A checagem de resultados agora simula <strong>SL/TP de {PERCENTUAL_SL_TP * 100:.2f}%</strong>."
                )
            sinal_exibicao = 'SEM SINAL DE ENTRADA'
            sinal_cor_fundo = 'var(--neutro-fundo)'
            sinal_cor_borda = 'var(--neutro-borda)'
            
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
            ultimo_sinal_texto = '🟡 Nenhuma Entrada Forte Registrada (Pós-Filtro)'

        # Prepara detalhes do sinal e histórico
        if ULTIMO_SINAL['score'] != 0 or 'Filtrado' in ULTIMO_SINAL['sinal']:
            signal_details_html = f"""
                <div class="data-item">Horário do Sinal Ativo: <strong>{horario_exibicao}</strong></div>
                <div class="data-item">Preço de Entrada: <strong>{ULTIMO_SINAL['preco_entrada']:.5f}</strong></div>
                <div class="data-item">Força (Score M1): <strong>{ULTIMO_SINAL['score']}</strong></div>
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
            'isSinalForte': is_sinal_forte, 
            'dynamicCss': dynamic_style_css
        }
        return data_payload

# ====================== ROTA DE STREAM SSE (MELHORIA UX) ======================
@app.route('/stream')
def stream():
    def event_stream():
        while True:
            try:
                data = render_dashboard_content()
                yield f"data: {json.dumps(data)}\n\n"
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
        tra
