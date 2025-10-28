# main.py
# ROBÔ TRADER M1 (WEB) - VERSÃO ASSERTIVIDADE POR CONFLUÊNCIA (3 REGRAS)
# Implementa: Filtro de 3 Regras (Momentum, Bollinger, RSI) e Assertividade de 100%.

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
# INTERVALO_M5 foi removido
NUM_VELAS_ANALISE_M1 = 30 
ASSERTIVIDADE_MINIMA = 100.0 # Requer 100% de confluência (3/3 regras)
MAX_HISTORICO = 10
PERCENTUAL_SL_TP = 0.0005 
# Configurações dos Indicadores
PERIOD_BB = 14
STD_DEV_BB = 2
PERIOD_RSI = 14
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0

# Intervalo de atualização do dashboard via SSE
DASHBOARD_REFRESH_RATE_SECONDS = 5 
URL_ALERTE_SONORO = "https://www.soundhelix.com/examples/audio/Wave-beep.wav"

# ====================== INICIALIZAÇÃO DO FLASK ======================
app = Flask(__name__)
state_lock = Lock()

# ====================== VARIÁVEIS GLOBAIS DE ESTADO ======================
def get_horario_brasilia():
    fuso_brasil = pytz.timezone(TIMEZONE_BR)
    return datetime.now(fuso_brasil)

ULTIMO_SINAL = {
    'horario': get_horario_brasilia().strftime('%H:%M:%S'),
    'ativo': 'N/A',
    'sinal': 'NEUTRO 🟡',
    'assertividade': 0.0,
    'preco_entrada': 0.0
}

ULTIMO_SINAL_REGISTRADO = {'horario': 'N/A', 'sinal_tipo': 'N/A'}
HISTORICO_SINAIS = []
ULTIMO_SINAL_CHECAR = None

# ====================== CÁLCULO DE INDICADORES (Manual, sem numpy) ======================

def calculate_rsi(velas, period=PERIOD_RSI):
    """Calcula o RSI de 14 períodos."""
    if len(velas) < period:
        return 50.0 # Neutro
    
    # Preços de fechamento
    closes = [v[1] for v in velas]
    
    gains = []
    losses = []
    
    # Diferença entre os fechamentos (Change)
    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]

    # Acúmulo dos 14 primeiros para a média inicial
    initial_gains = [c for c in changes[0:period] if c > 0]
    initial_losses = [abs(c) for c in changes[0:period] if c < 0]

    avg_gain = sum(initial_gains) / period if initial_gains else 0
    avg_loss = sum(initial_losses) / period if initial_losses else 0

    if avg_loss == 0:
        return 100.0 # Força total de alta (preço de fechamento da última vela)

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_bollinger_bands(velas, period=PERIOD_BB, std_dev=STD_DEV_BB):
    """Calcula Bandas de Bollinger (SMA, StdDev, Upper/Lower Band)."""
    if len(velas) < period:
        # Retorna bandas em torno do último preço
        last_close = velas[-1][1] if velas else 0.0
        return {'upper': last_close * 1.001, 'mid': last_close, 'lower': last_close * 0.999} 

    closes = [v[1] for v in velas[-period:]]
    
    # 1. Média Móvel Simples (SMA) - Banda do Meio
    sma = sum(closes) / period
    
    # 2. Desvio Padrão
    variance = sum([(c - sma) ** 2 for c in closes]) / period
    std_dev_val = variance ** 0.5
    
    # 3. Bandas
    upper_band = sma + (std_dev_val * std_dev)
    lower_band = sma - (std_dev_val * std_dev)
    
    return {'upper': upper_band, 'mid': sma, 'lower': lower_band}

# ====================== FUNÇÕES BASE E DE DADOS ======================

def calcular_assertividade():
    with state_lock:
        if not HISTORICO_SINAIS:
            return {'total': 0, 'wins': 0, 'losses': 0, 'percentual': 'N/A'}

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
        # Não usa mais o INTERVALO_M5, então o limite é sempre NUM_VELAS_ANALISE_M1
        params = {'symbol': ativo, 'type': intervalo, 'limit': NUM_VELAS_ANALISE_M1} 
        r = requests.get(API_BASE_URL, params=params, timeout=8)
        r.raise_for_status()
        data = r.json().get('data', [])

        velas = []
        # KuCoin API: [timestamp, open, close, high, low, volume, turnover]
        # Nosso formato interno: [Open, Close, High, Low]
        for v in data:
            # Indices KuCoin: v[1]=open, v[2]=close, v[3]=high, v[4]=low
            velas.append([float(v[1]), float(v[2]), float(v[3]), float(v[4])])
        
        return velas

    except Exception as e:
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Erro ao obter velas {intervalo} de {ativo}: {e}")
        return []
    return []

# A função get_tendencia_m5 foi removida.

def checar_resultado_sinal(sinal_checar):
    """Simula o resultado considerando SL/TP de 0.05% com base nos High/Low da vela de expiração."""
    global HISTORICO_SINAIS
    try:
        ativo = sinal_checar['ativo']
        preco_entrada = sinal_checar['preco_entrada']
        direcao_sinal = sinal_checar['sinal']
        if ativo == 'N/A' or 'NEUTRO' in direcao_sinal or sinal_checar['assertividade'] < ASSERTIVIDADE_MINIMA:
            return
        
        # Pega a vela que serviu de expiração (a última vela fechada)
        velas_exp = get_velas_kucoin(ativo, INTERVALO_M1)
        if len(velas_exp) < 1:
            return
        
        # Dados da Vela de Expiração: [Open, Close, High, Low]
        # A última vela é a que fecha 1 min após o sinal
        o_exp, c_exp, h_exp, l_exp = velas_exp[-1] 
        resultado = 'NEUTRO'
        
        percentual_sl_tp = PERCENTUAL_SL_TP

        # ====================== Lógica de Checagem SL/TP ======================
        if 'COMPRA' in direcao_sinal: 
            tp_price = preco_entrada * (1 + percentual_sl_tp)
            sl_price = preco_entrada * (1 - percentual_sl_tp)
            
            if h_exp >= tp_price:
                resultado = 'WIN ✅ (TP)'
            elif l_exp <= sl_price:
                resultado = 'LOSS ❌ (SL)'
            else:
                resultado = 'WIN ✅ (Close)' if c_exp > preco_entrada else 'LOSS ❌ (Close)'
                
        elif 'VENDA' in direcao_sinal: 
            tp_price = preco_entrada * (1 - percentual_sl_tp)
            sl_price = preco_entrada * (1 + percentual_sl_tp)

            if l_exp <= tp_price:
                resultado = 'WIN ✅ (TP)'
            elif h_exp >= sl_price:
                resultado = 'LOSS ❌ (SL)'
            else:
                resultado = 'WIN ✅ (Close)' if c_exp < preco_entrada else 'LOSS ❌ (Close)'
        # ======================================================================

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
        
        resultado_formatado = item['resultado'].replace(' (Close)', '')

        linha = (
            f"[{item['horario']}] {item['ativo']} -> "
            f"<span class='{classe}'>{resultado_formatado}</span> "
            f"(Assertividade: {item['assertividade']:.0f}%. Fechamento Diff: {sinal_diff}{diferenca:.5f})"
        )
        linhas_html.append(linha)
    return '\n'.join(linhas_html)

# ====================== ESTRATÉGIA CENTRAL DE ASSERTIVIDADE (3 REGRAS) ======================

def calcular_assertividade_confluencia(ativo, velas_m1):
    """
    Calcula a Assertividade (Confluência) para COMPRA e VENDA.
    Assertividade = 100% se 3/3 regras forem convergentes.
    """
    if len(velas_m1) < NUM_VELAS_ANALISE_M1:
        return {'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0}

    # A vela que está fechando é a última do array
    preco_entrada = velas_m1[-1][1] 
    
    # Preço High/Low da vela que está fechando (para Bollinger)
    h_atual = velas_m1[-1][2]
    l_atual = velas_m1[-1][3]
    
    # ------------------ INDICADORES ------------------
    rsi_val = calculate_rsi(velas_m1)
    bb_bands = calculate_bollinger_bands(velas_m1)
    
    # ------------------ CHECAGEM DE REGRA (Momentum M1) ------------------
    
    # 1. Momentum M1: Pelo menos 2 velas anteriores na mesma direção
    o1, c1 = velas_m1[-1][0], velas_m1[-1][1] # Atual
    o2, c2 = velas_m1[-2][0], velas_m1[-2][1] # Penúltima

    # Determina o bias (viés de momentum)
    momentum_buy = (c1 > o1) and (c2 > o2)
    momentum_sell = (c1 < o1) and (c2 < o2)

    # Função interna para calcular a assertividade para uma direção específica
    def check_direction_confluence(direcao, has_momentum):
        passed_rules = 0
        
        # REGRA 1: Momentum M1 (33.33%) - Condição inicial para considerar a entrada
        if has_momentum:
            passed_rules += 1
        else:
            return 0.0 # Sem momentum, assertividade zero.

        # REGRA 2: Bandas de Bollinger (33.33%)
        # COMPRA: Tocou ou está próximo da banda inferior (Sinal de reversão altista)
        if direcao == 'COMPRA' and l_atual <= bb_bands['lower']:
            passed_rules += 1
        # VENDA: Tocou ou está próximo da banda superior (Sinal de reversão baixista)
        elif direcao == 'VENDA' and h_atual >= bb_bands['upper']:
            passed_rules += 1
            
        # REGRA 3: RSI (33.33%) - Sobrevendido/Sobrecomprado
        # COMPRA: Sobrevendido
        if direcao == 'COMPRA' and rsi_val <= RSI_OVERSOLD:
            passed_rules += 1
        # VENDA: Sobrecomprado
        elif direcao == 'VENDA' and rsi_val >= RSI_OVERBOUGHT:
            passed_rules += 1
            
        # Assertividade é a porcentagem de regras passadas (máximo de 3 regras)
        return (passed_rules / 3.0) * 100.0

    # ------------------ ANÁLISE CRUZADA E FILTRO ------------------
    assert_buy = check_direction_confluence('COMPRA', momentum_buy)
    assert_sell = check_direction_confluence('VENDA', momentum_sell)
    
    final_sinal = 'NEUTRO 🟡'
    final_assertividade = 0.0
    
    # Prioriza o sinal com maior assertividade, mas exige o mínimo de 100%
    if assert_buy >= ASSERTIVIDADE_MINIMA and assert_buy >= assert_sell:
        final_sinal = 'COMPRA FORTE 🚀'
        final_assertividade = assert_buy
    elif assert_sell >= ASSERTIVIDADE_MINIMA and assert_sell >= assert_buy:
        final_sinal = 'VENDA FORTE 📉'
        final_assertividade = assert_sell
    else:
        final_assertividade = max(assert_buy, assert_sell)
        if final_assertividade < ASSERTIVIDADE_MINIMA and final_assertividade > 0:
            # Entrada bloqueada, mas registra a assertividade máxima encontrada
            final_sinal = 'NEUTRO (Assertividade Insuficiente)' 
        else:
            final_sinal = 'NEUTRO 🟡'


    return {'sinal': final_sinal, 'assertividade': final_assertividade, 'preco_entrada': preco_entrada}


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
            
            if ULTIMO_SINAL_CHECAR:
                checar_resultado_sinal(ULTIMO_SINAL_CHECAR)
                with state_lock:
                    ULTIMO_SINAL_CHECAR = None

            now_dt = get_horario_brasilia()
            horario_atual_str = now_dt.strftime('%H:%M:%S')

            print(f"[{horario_atual_str}] Iniciando novo ciclo de análise...")
            
            melhor = {'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0}

            for ativo in ATIVOS_MONITORADOS:
                velas_m1 = get_velas_kucoin(ativo, INTERVALO_M1)
                analise_confluencia = calcular_assertividade_confluencia(ativo, velas_m1)
                
                # Encontra o ativo com a maior assertividade (mesmo que não seja 100%)
                if analise_confluencia['assertividade'] >= melhor['assertividade']:
                    melhor = {'ativo': ativo, **analise_confluencia}

            sinal_final = {
                'horario': horario_atual_str,
                'ativo': melhor['ativo'],
                'sinal': melhor['sinal'],
                'assertividade': melhor['assertividade'],
                'preco_entrada': melhor['preco_entrada']
            }
            
            with state_lock:
                # Se encontrou um sinal com a assertividade mínima
                if sinal_final['assertividade'] >= ASSERTIVIDADE_MINIMA:
                    ULTIMO_SINAL_CHECAR = copy.deepcopy(sinal_final)
                    ULTIMO_SINAL_REGISTRADO.update({
                        'horario': sinal_final['horario'],
                        'sinal_tipo': 'COMPRA' if 'COMPRA' in sinal_final['sinal'] else 'VENDA'
                    })

                ULTIMO_SINAL.update(sinal_final)

            print(f"[{horario_atual_str}] 📢 Novo Sinal: {ULTIMO_SINAL['ativo']} - {ULTIMO_SINAL['sinal']} (Assertividade: {ULTIMO_SINAL['assertividade']:.0f}%)")

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
        
        is_sinal_forte = ULTIMO_SINAL['assertividade'] >= ASSERTIVIDADE_MINIMA

        if 'COMPRA FORTE' in ULTIMO_SINAL['sinal'] and is_sinal_forte:
            sinal_cor_fundo = 'var(--compra-fundo)' 
            sinal_cor_borda = 'var(--compra-borda)' 
            sinal_classe_animacao = 'signal-active'
            explicacao = (
                f"Entrada de <strong>COMPRA FORTE</strong> em <strong>{ULTIMO_SINAL['ativo']}</strong>."
                f"<br><strong>Assertividade: {ULTIMO_SINAL['assertividade']:.0f}% (Confluência MÁXIMA).</strong>"
                f"<br>Regras ativadas: Momentum Altista + BB Inferior Tocado + RSI Sobrevendido."
            )
        elif 'VENDA FORTE' in ULTIMO_SINAL['sinal'] and is_sinal_forte:
            sinal_cor_fundo = 'var(--venda-fundo)' 
            sinal_cor_borda = 'var(--venda-borda)' 
            sinal_classe_animacao = 'signal-active'
            explicacao = (
                f"Entrada de <strong>VENDA FORTE</strong> em <strong>{ULTIMO_SINAL['ativo']}</strong>."
                f"<br><strong>Assertividade: {ULTIMO_SINAL['assertividade']:.0f}% (Confluência MÁXIMA).</strong>"
                f"<br>Regras ativadas: Momentum Baixista + BB Superior Tocado + RSI Sobrecomprado."
            )
        else:
            if ULTIMO_SINAL['assertividade'] > 0 and ULTIMO_SINAL['assertividade'] < ASSERTIVIDADE_MINIMA:
                 explicacao = (
                    f"Entrada em <strong>{ULTIMO_SINAL['ativo']}</strong> bloqueada."
                    f"<br>Assertividade encontrada: <strong>{ULTIMO_SINAL['assertividade']:.0f}%</strong>."
                    f"<br><strong>Entrada não aprovada devido à assertividade insuficiente (<{ASSERTIVIDADE_MINIMA:.0f}%).</strong>"
                )
                 sinal_exibicao = 'ENTRADA BLOQUEADA'
            else:
                explicacao = (
                    "No momento, o robô está em <strong>NEUTRO</strong>. Nenhuma confluência foi encontrada."
                    f"<br>O robô exige <strong>{ASSERTIVIDADE_MINIMA:.0f}% de Assertividade</strong> (todas as 3 regras ativadas) para operar."
                )
                sinal_exibicao = 'SEM SINAL DE ENTRADA'
            sinal_cor_fundo = 'var(--neutro-fundo)'
            sinal_cor_borda = 'var(--neutro-borda)'
            
        # --- Fim Lógica de Cor e Animação ---

        ultimo_sinal_hora = ULTIMO_SINAL_REGISTRADO['horario']
        ultimo_sinal_tipo = ULTIMO_SINAL_REGISTRADO['sinal_tipo']

        if ultimo_sinal_tipo == 'COMPRA':
            ultimo_sinal_cor_css = 'var(--compra-borda)'
            ultimo_sinal_texto = f'✅ Última Entrada Registrada: COMPRA (Horário: {ultimo_sinal_hora})'
        elif ultimo_sinal_tipo == 'VENDA':
            ultimo_sinal_cor_css = 'var(--venda-borda)'
            ultimo_sinal_texto = f'❌ Última Entrada Registrada: VENDA (Horário: {ultimo_sinal_hora})'
        else:
            ultimo_sinal_cor_css = 'var(--neutro-borda)'
            ultimo_sinal_texto = '🟡 Nenhuma Entrada Registrada (Aguardando 100% de Assertividade)'

        # Prepara detalhes do sinal e histórico
        if ULTIMO_SINAL['assertividade'] > 0:
            signal_details_html = f"""
                <div class="data-item">Horário da Análise: <strong>{horario_exibicao}</strong></div>
                <div class="data-item">Preço de Entrada: <strong>{ULTIMO_SINAL['preco_entrada']:.5f}</strong></div>
            """
            analise_detail_html = f"""
                <div class="assertividade-score">ASSERTIVIDADE: <span style="font-size:1.5em; font-weight:700;">{ULTIMO_SINAL['assertividade']:.0f}%</span></div>
            """
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
