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
    if len(velas) < period + 1: # Precisa de pelo menos 15 velas para o primeiro cálculo
        return 50.0 # Neutro
    
    # Preços de fechamento
    closes = [v[1] for v in velas]
    
    # Diferença entre os fechamentos (Change)
    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]

    # Acúmulo dos 14 primeiros para a média inicial
    initial_changes = changes[len(changes) - period: len(changes)] # Pega as últimas 14 mudanças
    
    initial_gains = [c for c in initial_changes if c > 0]
    initial_losses = [abs(c) for c in initial_changes if c < 0]

    # Para um cálculo mais preciso (SMA simples nas primeiras 14)
    avg_gain = sum(initial_gains) / period
    avg_loss = sum(initial_losses) / period

    if avg_loss == 0:
        return 100.0 # Força total de alta
    
    # Cálculo Suavizado (SMMA/Wilder's Smoothing) - Simplificado para apenas a última RS
    # Para o propósito deste monitor, vamos usar apenas a RS da última janela
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_bollinger_bands(velas, period=PERIOD_BB, std_dev=STD_DEV_BB):
    """Calcula Bandas de Bollinger (SMA, StdDev, Upper/Lower Band)."""
    if len(velas) < period:
        # Retorna bandas em torno do último preço
        last_close = velas[-1][1] if velas else 0.0
        # Mínimo de 1% de banda para evitar divisão por zero ou bandas muito estreitas
        band_diff = last_close * 0.01 
        return {'upper': last_close + band_diff, 'mid': last_close, 'lower': last_close - band_diff} 

    # Usa apenas o período necessário (as últimas `period` velas)
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
        # Garante velas suficientes para análise (BB/RSI precisam de histórico)
        limit_velas = max(NUM_VELAS_ANALISE_M1, PERIOD_RSI + 1, PERIOD_BB) 
        
        params = {'symbol': ativo, 'type': intervalo, 'limit': limit_velas} 
        r = requests.get(API_BASE_URL, params=params, timeout=8)
        r.raise_for_status()
        data = r.json().get('data', [])

        velas = []
        # KuCoin API: [timestamp, open, close, high, low, volume, turnover]
        # Nosso formato interno: [Open, Close, High, Low]
        for v in data:
            # Indices KuCoin: v[1]=open, v[2]=close, v[3]=high, v[4]=low
            velas.append([float(v[1]), float(v[2]), float(v[3]), float(v[4])])
        
        # Inverte para ter a vela mais antiga primeiro (facilita o cálculo de RSI)
        return velas[::-1] 

    except Exception as e:
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Erro ao obter velas {intervalo} de {ativo}: {e}")
        return []
    return []

def checar_resultado_sinal(sinal_checar):
    """Simula o resultado considerando SL/TP de 0.05% com base nos High/Low da vela de expiração."""
    global HISTORICO_SINAIS, ULTIMO_SINAL_CHECAR
    try:
        ativo = sinal_checar['ativo']
        preco_entrada = sinal_checar['preco_entrada']
        direcao_sinal = sinal_checar['sinal']
        
        # Filtra sinais inválidos/neutros
        if ativo == 'N/A' or 'NEUTRO' in direcao_sinal or sinal_checar['assertividade'] < ASSERTIVIDADE_MINIMA:
            with state_lock:
                ULTIMO_SINAL_CHECAR = None
            return
        
        # Pega a vela que serviu de expiração (a última vela fechada)
        # Precisamos de 1 vela fechada APÓS o sinal (a vela atual do sinal é a penúltima)
        velas_exp = get_velas_kucoin(ativo, INTERVALO_M1)
        if len(velas_exp) < 2: # Pelo menos 2 velas para ter a de entrada e a de expiração
            return
        
        # Dados da Vela de Expiração: [Open, Close, High, Low]
        # A última vela é a que fechou após 1 min do sinal
        o_exp, c_exp, h_exp, l_exp = velas_exp[-1] 
        resultado = 'NEUTRO'
        
        percentual_sl_tp = PERCENTUAL_SL_TP

        # ====================== Lógica de Checagem SL/TP ======================
        if 'COMPRA' in direcao_sinal: 
            tp_price = preco_entrada * (1 + percentual_sl_tp)
            sl_price = preco_entrada * (1 - percentual_sl_tp)
            
            # Checa se o TP foi atingido (preço máximo da vela)
            if h_exp >= tp_price:
                resultado = 'WIN ✅ (TP)'
            # Checa se o SL foi atingido (preço mínimo da vela)
            elif l_exp <= sl_price:
                resultado = 'LOSS ❌ (SL)'
            # Se não atingiu TP nem SL, usa o fechamento da vela
            else:
                resultado = 'WIN ✅ (Close)' if c_exp > preco_entrada else 'LOSS ❌ (Close)'
                
        elif 'VENDA' in direcao_sinal: 
            tp_price = preco_entrada * (1 - percentual_sl_tp)
            sl_price = preco_entrada * (1 + percentual_sl_tp)

            # Checa se o TP foi atingido (preço mínimo da vela)
            if l_exp <= tp_price:
                resultado = 'WIN ✅ (TP)'
            # Checa se o SL foi atingido (preço máximo da vela)
            elif h_exp >= sl_price:
                resultado = 'LOSS ❌ (SL)'
            # Se não atingiu TP nem SL, usa o fechamento da vela
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
            
            # Limpa o sinal para checar, indicando que a checagem foi concluída
            ULTIMO_SINAL_CHECAR = None

        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] 🎯 Resultado de {ativo} ({sinal_checar['horario']}): {resultado}")
    except Exception:
        print("Erro em checar_resultado_sinal:")
        traceback.print_exc()
        with state_lock:
            # Em caso de erro, limpa para não travar a checagem
            ULTIMO_SINAL_CHECAR = None 

def formatar_historico_html(historico):
    linhas_html = []
    for item in reversed(historico):
        classe = 'win' if 'WIN' in item['resultado'] else 'loss'
        
        # Diferença percentual
        diff_abs = abs(item['preco_expiracao'] - item['preco_entrada'])
        diff_percentual = (diff_abs / item['preco_entrada']) * 100
        
        # Seta de direção
        if 'COMPRA' in item['sinal']:
            sinal_diff_icon = '⬆️' if 'WIN' in item['resultado'] else '⬇️'
        else: # VENDA
            sinal_diff_icon = '⬇️' if 'WIN' in item['resultado'] else '⬆️'

        resultado_formatado = item['resultado'].replace(' (Close)', '')

        linha = (
            f"<div class='historico-line {classe}'>"
            f"<span>{item['horario']}</span>"
            f"<span>{item['ativo']}</span>"
            f"<span class='sinal-dir'>{item['sinal'].replace(' FORTE', '')}</span>"
            f"<span class='resultado'>{resultado_formatado}</span>"
            f"<span class='diff'>{sinal_diff_icon} {diff_percentual:.4f}%</span>"
            f"</div>"
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

    # A vela que serviu de gatilho para a próxima entrada (a penúltima vela)
    # A entrada ocorre no preço de fechamento desta vela.
    penultima_vela = velas_m1[-2] 
    
    preco_entrada = penultima_vela[1] # Fechamento da vela -2 é o preço de entrada
    
    # Preço High/Low da vela que está fechando (para Bollinger)
    h_atual = penultima_vela[2]
    l_atual = penultima_vela[3]
    
    # ------------------ INDICADORES ------------------
    # O cálculo de indicadores deve usar o array completo (incluindo a vela de gatilho)
    rsi_val = calculate_rsi(velas_m1)
    bb_bands = calculate_bollinger_bands(velas_m1)
    
    # ------------------ CHECAGEM DE REGRA (Momentum M1) ------------------
    
    # 1. Momentum M1: Pelo menos 2 velas *antes* da vela de gatilho na mesma direção
    o_prev, c_prev = velas_m1[-3][0], velas_m1[-3][1] # Antepenúltima
    o_prev2, c_prev2 = velas_m1[-4][0], velas_m1[-4][1] # Quarta última

    # Regra 1: Momentum Altista (duas velas fechando verde antes do gatilho)
    momentum_buy = (c_prev > o_prev) and (c_prev2 > o_prev2)
    # Regra 1: Momentum Baixista (duas velas fechando vermelho antes do gatilho)
    momentum_sell = (c_prev < o_prev) and (c_prev2 < o_prev2)

    # Função interna para calcular a assertividade para uma direção específica
    def check_direction_confluence(direcao, has_momentum):
        passed_rules = 0
        rules_log = []
        
        # REGRA 1: Momentum M1 (33.33%) - Condição inicial para considerar a entrada
        if has_momentum:
            passed_rules += 1
            rules_log.append('Momentum (2 Velas) OK')
        else:
            return 0.0, [] # Sem momentum, assertividade zero.

        # REGRA 2: Bandas de Bollinger (33.33%)
        # COMPRA: Vela de Gatilho (penúltima) Tocou ou está abaixo da banda inferior (Sinal de reversão altista)
        if direcao == 'COMPRA' and l_atual <= bb_bands['lower']:
            passed_rules += 1
            rules_log.append('BB (Abaixo da banda inferior)')
        # VENDA: Vela de Gatilho (penúltima) Tocou ou está acima da banda superior (Sinal de reversão baixista)
        elif direcao == 'VENDA' and h_atual >= bb_bands['upper']:
            passed_rules += 1
            rules_log.append('BB (Acima da banda superior)')
            
        # REGRA 3: RSI (33.33%) - Sobrevendido/Sobrecomprado
        # COMPRA: Sobrevendido
        if direcao == 'COMPRA' and rsi_val <= RSI_OVERSOLD:
            passed_rules += 1
            rules_log.append(f'RSI ({rsi_val:.2f}) Sobrevendido')
        # VENDA: Sobrecomprado
        elif direcao == 'VENDA' and rsi_val >= RSI_OVERBOUGHT:
            passed_rules += 1
            rules_log.append(f'RSI ({rsi_val:.2f}) Sobrecomprado')
            
        # Assertividade é a porcentagem de regras passadas (máximo de 3 regras)
        return (passed_rules / 3.0) * 100.0, rules_log

    # ------------------ ANÁLISE CRUZADA E FILTRO ------------------
    assert_buy, rules_buy = check_direction_confluence('COMPRA', momentum_buy)
    assert_sell, rules_sell = check_direction_confluence('VENDA', momentum_sell)
    
    final_sinal = 'NEUTRO 🟡'
    final_assertividade = 0.0
    final_rules = []
    
    # Prioriza o sinal com maior assertividade, mas exige o mínimo de 100%
    if assert_buy >= ASSERTIVIDADE_MINIMA and assert_buy >= assert_sell:
        final_sinal = 'COMPRA FORTE 🚀'
        final_assertividade = assert_buy
        final_rules = rules_buy
    elif assert_sell >= ASSERTIVIDADE_MINIMA and assert_sell >= assert_buy:
        final_sinal = 'VENDA FORTE 📉'
        final_assertividade = assert_sell
        final_rules = rules_sell
    else:
        final_assertividade = max(assert_buy, assert_sell)
        final_rules = rules_buy if assert_buy > assert_sell else rules_sell
        if final_assertividade < ASSERTIVIDADE_MINIMA and final_assertividade > 0:
            # Entrada bloqueada, mas registra a assertividade máxima encontrada
            final_sinal = 'NEUTRO (Assertividade Insuficiente)' 
        else:
            final_sinal = 'NEUTRO 🟡'
            final_rules = []


    return {
        'sinal': final_sinal, 
        'assertividade': final_assertividade, 
        'preco_entrada': preco_entrada,
        'rules': final_rules
    }


# ====================== CICLO DE ANÁLISE (BACKGROUND) ======================
def ciclo_analise():
    global ULTIMO_SINAL, ULTIMO_SINAL_CHECAR, ULTIMO_SINAL_REGISTRADO
    # Espera até o início do próximo minuto para sincronizar com o fechamento das velas
    time.sleep(60 - datetime.now().second) 
    
    while True:
        try:
            # Checa o resultado do sinal anterior (a vela de expiração deve ter fechado agora)
            if ULTIMO_SINAL_CHECAR:
                checar_resultado_sinal(ULTIMO_SINAL_CHECAR)
                # O ULTIMO_SINAL_CHECAR é zerado dentro da função checar_resultado_sinal

            now_dt = get_horario_brasilia()
            horario_atual_str = now_dt.strftime('%H:%M:%S')

            print(f"[{horario_atual_str}] Iniciando novo ciclo de análise...")
            
            melhor = {'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0, 'rules': []}

            for ativo in ATIVOS_MONITORADOS:
                velas_m1 = get_velas_kucoin(ativo, INTERVALO_M1)
                
                # Garante que há velas suficientes para o cálculo do gatilho (mínimo 4)
                if len(velas_m1) < 4: 
                    print(f"[{horario_atual_str}] ⚠️ {ativo}: Velas insuficientes para análise. Pulando.")
                    continue
                    
                analise_confluencia = calcular_assertividade_confluencia(ativo, velas_m1)
                
                # Encontra o ativo com a maior assertividade (mesmo que não seja 100%)
                if analise_confluencia['assertividade'] >= melhor['assertividade']:
                    melhor = {'ativo': ativo, **analise_confluencia}

            sinal_final = {
                'horario': horario_atual_str,
                'ativo': melhor['ativo'],
                'sinal': melhor['sinal'],
                'assertividade': melhor['assertividade'],
                'preco_entrada': melhor['preco_entrada'],
                'rules': melhor['rules']
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
            
            # Garante que o sleep time sincronize para o próximo minuto
            now_dt = get_horario_brasilia()
            seconds_until_next_minute = 60 - now_dt.second
            sleep_time = seconds_until_next_minute if seconds_until_next_minute > 0 else 60
            
            time.sleep(sleep_time)


        except Exception:
            print("Erro no ciclo_analise:")
            traceback.print_exc()
            time.sleep(10) # Pausa em caso de erro

# Inicia a thread de análise em segundo plano
analysis_thread = Thread(target=ciclo_analise, daemon=True)
analysis_thread.start()

# ====================== GERAÇÃO DINÂMICA DO CONTEÚDO (Para SSE) ======================
def render_dashboard_content():
    """Gera o JSON de estado do dashboard para o SSE."""
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

        rules_list_html = "".join([f"<li>{r}</li>" for r in ULTIMO_SINAL['rules']])
        
        if 'COMPRA FORTE' in ULTIMO_SINAL['sinal'] and is_sinal_forte:
            sinal_cor_fundo = 'var(--compra-fundo)' 
            sinal_cor_borda = 'var(--compra-borda)' 
            sinal_classe_animacao = 'signal-active'
            explicacao = (
                f"<p>Entrada de <strong>COMPRA FORTE</strong> em <strong>{ULTIMO_SINAL['ativo']}</strong>.</p>"
                f"<p><strong>Assertividade: {ULTIMO_SINAL['assertividade']:.0f}% (Confluência MÁXIMA).</strong></p>"
                f"<div class='rule-list-container'><p>Regras Ativadas:</p><ul>{rules_list_html}</ul></div>"
            )
        elif 'VENDA FORTE' in ULTIMO_SINAL['sinal'] and is_sinal_forte:
            sinal_cor_fundo = 'var(--venda-fundo)' 
            sinal_cor_borda = 'var(-
