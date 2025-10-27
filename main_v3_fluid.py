# main_v4_dashboard.py
# ROBÔ TRADER M1 (FLUIDO) - ESTRATÉGIA RSI/BB/MOMENTUM
# Combina a paralelização de I/O da v3 com um Dashboard esteticamente aprimorado (similar ao seu pedido).

from flask import Flask, Response
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
MAX_WORKERS = 5 # Número máximo de requisições de API simultâneas

# Configurações da Estratégia de Confluência
ASSERTIVIDADE_MINIMA = 80.0  # Requer 100% de confluência (3/3 regras)
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

# ====================== CÁLCULO DE INDICADORES (Manual) ======================

def calculate_rsi(velas, period=PERIOD_RSI):
    """Calcula o RSI de 14 períodos."""
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
    """Calcula Bandas de Bollinger (SMA, StdDev, Upper/Lower Band)."""
    if len(velas) < period:
        last_close = velas[-1][1] if velas else 0.0
        return {'upper': last_close * 1.001, 'mid': last_close, 'lower': last_close * 0.999} 

    closes = [v[1] for v in velas[-period:]]
    sma = sum(closes) / period
    variance = sum([(c - sma) ** 2 for c in closes]) / period
    std_dev_val = variance ** 0.5
    
    upper_band = sma + (std_dev_val * std_dev)
    lower_band = sma - (std_dev_val * std_dev)
    
    return {'upper': upper_band, 'mid': sma, 'lower': lower_band}

# ====================== FUNÇÕES BASE E DE DADOS ======================

def calcular_assertividade_historico():
    with state_lock:
        if not HISTORICO_SINAIS:
            return {'total': 0, 'wins': 0, 'losses': 0, 'percentual': 'N/A'}

        wins = sum(1 for item in HISTORICO_SINAIS if 'WIN' in item['resultado'])
        total = len(HISTORICO_SINAIS)
        losses = total - wins
        percentual = f"{(wins / total) * 100:.2f}%" if total else 'N/A'
        return {'total': total, 'wins': wins, 'losses': losses, 'percentual': percentual}

def get_velas_kucoin(ativo, intervalo, limit=NUM_VELAS_ANALISE_M1):
    """Busca as velas da KuCoin para um dado ativo e intervalo."""
    try:
        params = {'symbol': ativo, 'type': intervalo, 'limit': limit} 
        r = requests.get(API_BASE_URL, params=params, timeout=8)
        r.raise_for_status()
        data = r.json().get('data', [])

        velas = []
        for v in data:
            # Indices KuCoin: v[1]=open, v[2]=close, v[3]=high, v[4]=low
            velas.append([float(v[1]), float(v[2]), float(v[3]), float(v[4])])
        
        return velas

    except Exception as e:
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Erro ao obter velas {intervalo} de {ativo}: {e}")
        return []
    return []

def checar_resultado_sinal(sinal_checar):
    """Simula o resultado considerando SL/TP de 0.05% na vela de expiração."""
    global HISTORICO_SINAIS
    try:
        ativo = sinal_checar['ativo']
        preco_entrada = sinal_checar['preco_entrada']
        direcao_sinal = sinal_checar['sinal']
        
        if ativo == 'N/A' or 'NEUTRO' in direcao_sinal or sinal_checar['assertividade'] < ASSERTIVIDADE_MINIMA:
            return
        
        # Limit=1 para pegar apenas a vela de expiração (o minuto seguinte)
        velas_exp = get_velas_kucoin(ativo, INTERVALO_M1, limit=1)
        if len(velas_exp) < 1:
            return
        
        o_exp, c_exp, h_exp, l_exp = velas_exp[-1] 
        resultado = 'NEUTRO'
        percentual_sl_tp = PERCENTUAL_SL_TP

        # Checagem SL/TP
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

        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] 🎯 Resultado de {ativo} ({sinal_checar['horario']}): {resultado}")
    except Exception:
        print("Erro em checar_resultado_sinal:")
        traceback.print_exc()

def formatar_historico_html(historico):
    linhas_html = []
    # Itera de trás para frente para mostrar os mais novos primeiro
    for item in reversed(historico):
        classe = 'win' if 'WIN' in item['resultado'] else 'loss'
        diferenca = item['preco_expiracao'] - item['preco_entrada']
        sinal_diff = "+" if diferenca >= 0 else ""
        resultado_formatado = item['resultado'].replace(' (Close)', '')

        linha = (
            f"[{item['horario']}] {item['ativo']} -> "
            f"<span class='{classe}'>{resultado_formatado}</span> "
            f"(Assertividade: {item['assertividade']:.0f}%. Diff: {sinal_diff}{diferenca:.5f})"
        )
        linhas_html.append(linha)
    return '\n'.join(linhas_html)

# ====================== ESTRATÉGIA CENTRAL DE ASSERTIVIDADE (3 REGRAS) ======================

def analisar_ativo(ativo):
    """Função wrapper para ser executada em paralelo."""
    velas_m1 = get_velas_kucoin(ativo, INTERVALO_M1)
    if not velas_m1 or len(velas_m1) < NUM_VELAS_ANALISE_M1:
        return {'ativo': ativo, 'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0}

    preco_entrada = velas_m1[-1][1] 
    o_atual, c_atual, h_atual, l_atual = velas_m1[-1]
    o2, c2 = velas_m1[-2][0], velas_m1[-2][1] # Penúltima vela

    rsi_val = calculate_rsi(velas_m1)
    bb_bands = calculate_bollinger_bands(velas_m1)
    
    # REGRA 1: Momentum M1 (2 velas fechando na mesma direção)
    momentum_buy = (c_atual > o_atual) and (c2 > o2)
    momentum_sell = (c_atual < o_atual) and (c2 < o2)

    def check_direction_confluence(direcao, has_momentum):
        passed_rules = 0
        
        # Regra 1: Momentum (33.33%)
        if has_momentum: passed_rules += 1
        else: return 0.0

        # Regra 2: Bandas de Bollinger (33.33%) - Tocou a banda de sobre-extensão
        if direcao == 'COMPRA' and l_atual <= bb_bands['lower']: passed_rules += 1
        elif direcao == 'VENDA' and h_atual >= bb_bands['upper']: passed_rules += 1
            
        # Regra 3: RSI (33.33%) - Sobrevenda/Sobrecompra
        if direcao == 'COMPRA' and rsi_val <= RSI_OVERSOLD: passed_rules += 1
        elif direcao == 'VENDA' and rsi_val >= RSI_OVERBOUGHT: passed_rules += 1
            
        return (passed_rules / 3.0) * 100.0

    assert_buy = check_direction_confluence('COMPRA', momentum_buy)
    assert_sell = check_direction_confluence('VENDA', momentum_sell)
    
    final_sinal = 'NEUTRO 🟡'
    final_assertividade = 0.0
    
    # Exige Assertividade Mínima de 80.0% (Confluência Total = 100%)
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


# ====================== CICLO DE ANÁLISE (BACKGROUND) - FLUIDO ======================
def ciclo_analise():
    global ULTIMO_SINAL, ULTIMO_SINAL_CHECAR, ULTIMO_SINAL_REGISTRADO
    time.sleep(1) 
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        while True:
            try:
                # --- AGENDAMENTO: Alinhado ao Minuto (XX:XX:00) ---
                now_dt = get_horario_brasilia()
                seconds_until_next_minute = 60 - now_dt.second
                sleep_time = seconds_until_next_minute if seconds_until_next_minute != 60 else 60
                
                # Checa o resultado do sinal anterior (se houver) ANTES de dormir
                if ULTIMO_SINAL_CHECAR:
                    checar_resultado_sinal(ULTIMO_SINAL_CHECAR)
                    with state_lock:
                        ULTIMO_SINAL_CHECAR = None

                time.sleep(sleep_time) # Dorme até o início do próximo minuto

                # --- ANÁLISE FLUIDA (PARALELIZADA) ---
                start_time = time.time()
                now_dt = get_horario_brasilia()
                horario_atual_str = now_dt.strftime('%H:%M:%S')

                print(f"[{horario_atual_str}] Iniciando novo ciclo de análise (PARALELO)...")
                
                # Executa a análise para todos os ativos em paralelo
                melhores_sinais = list(executor.map(analisar_ativo, ATIVOS_MONITORADOS))

                melhor = {'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0}

                # Coleta o melhor sinal
                for sinal in melhores_sinais:
                    if sinal['assertividade'] >= melhor['assertividade']:
                        melhor = sinal

                end_time = time.time()
                print(f"[{horario_atual_str}] Ciclo concluído em {end_time - start_time:.2f} segundos.")
                
                # --- ATUALIZAÇÃO DO ESTADO ---
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
                print("Erro no ciclo_analise:")
                traceback.print_exc()
                time.sleep(5) 

# Inicia a thread de análise em segundo plano
analysis_thread = Thread(target=ciclo_analise, daemon=True)
analysis_thread.start()

# ====================== GERAÇÃO DINÂMICA DO CONTEÚDO (Para SSE) ======================
def render_dashboard_content():
    """Gera o payload de dados para o dashboard via Server-Sent Events (SSE)."""
    with state_lock:
        assertividade_data = calcular_assertividade_historico()
        horario_atual_brasilia = get_horario_brasilia().strftime('%H:%M:%S')

        sinal_exibicao = ULTIMO_SINAL['sinal']
        horario_exibicao = ULTIMO_SINAL['horario']
        
        # --- Lógica de Cor e Animação ---
        sinal_cor_fundo = 'var(--neutro-fundo)'
        sinal_cor_borda = 'var(--neutro-borda)'
        sinal_classe_animacao = ''
        
        is_sinal_aprovado = ULTIMO_SINAL['assertividade'] >= ASSERTIVIDADE_MINIMA
        
        # --- Lógica de Geração da Explicação Resumida ---
        if 'COMPRA APROVADA' in ULTIMO_SINAL['sinal'] and is_sinal_aprovado:
            sinal_cor_fundo = 'var(--compra-fundo)' 
            sinal_cor_borda = 'var(--compra-borda)' 
            sinal_classe_animacao = 'signal-active'
            
            explicacao_resumida = (
                f"Ação: **COMPRA** (Entrada aprovada: Sim)\n"
                f"Horário de entrada sugerido: {get_horario_brasilia().strftime('%H:%M')} + 1 min\n"
                f"Explicação resumida: Momentum Altista confirmado (R1). Preço sobre-estendido na BB inferior (R2). RSI em zona de sobrevenda (<{RSI_OVERSOLD:.0f}) (R3)."
            )
            explicacao_dashboard = (
                f"Entrada de **COMPRA** aprovada em **{ULTIMO_SINAL['ativo']}**."
                f"<br><strong>Assertividade: {ULTIMO_SINAL['assertividade']:.0f}% (Confluência MÁXIMA).</strong>"
                f"<br>Regras ativadas: (R1) Momentum Altista + (R2) BB Inferior Tocado + (R3) RSI Sobrevendido."
            )
            
        elif 'VENDA APROVADA' in ULTIMO_SINAL['sinal'] and is_sinal_aprovado:
            sinal_cor_fundo = 'var(--venda-fundo)' 
            sinal_cor_borda = 'var(--venda-borda)' 
            sinal_classe_animacao = 'signal-active'
            
            explicacao_resumida = (
                f"Ação: **VENDA** (Entrada aprovada: Sim)\n"
                f"Horário de entrada sugerido: {get_horario_brasilia().strftime('%H:%M')} + 1 min\n"
                f"Explicação resumida: Momentum Baixista confirmado (R1). Preço sobre-estendido na BB superior (R2). RSI em zona de sobrecompra (>{RSI_OVERBOUGHT:.0f}) (R3)."
            )
            explicacao_dashboard = (
                f"Entrada de **VENDA** aprovada em **{ULTIMO_SINAL['ativo']}**."
                f"<br><strong>Assertividade: {ULTIMO_SINAL['assertividade']:.0f}% (Confluência MÁXIMA).</strong>"
                f"<br>Regras ativadas: (R1) Momentum Baixista + (R2) BB Superior Tocado + (R3) RSI Sobrecomprado."
            )
        else:
            if ULTIMO_SINAL['sinal'] == 'ENTRADA BLOQUEADA':
                 explicacao_resumida = (
                    f"Ação: **NEUTRO** (Entrada aprovada: Não)\n"
                    f"Horário de entrada sugerido: N/A\n"
                    f"Explicação resumida: Entrada bloqueada. Assertividade encontrada: {ULTIMO_SINAL['assertividade']:.0f}%. Precisa de {ASSERTIVIDADE_MINIMA:.0f}%."
                )
                 explicacao_dashboard = (
                    f"Entrada em **{ULTIMO_SINAL['ativo']}** bloqueada."
                    f"<br>Assertividade encontrada: **{ULTIMO_SINAL['assertividade']:.0f}%**."
                    f"<br><strong>Entrada não aprovada devido à assertividade insuficiente (<{ASSERTIVIDADE_MINIMA:.0f}%).</strong>"
                )
                 sinal_exibicao = 'ENTRADA BLOQUEADA'
            else:
                explicacao_resumida = (
                    f"Ação: **NEUTRO** (Entrada aprovada: Não)\n"
                    f"Horário de entrada sugerido: N/A\n"
                    f"Explicação resumida: Nenhuma confluência encontrada no momento."
                )
                explicacao_dashboard = (
                    "No momento, o robô está em **NEUTRO**. Nenhuma confluência foi encontrada."
                    f"<br>O robô exige **{ASSERTIVIDADE_MINIMA:.0f}% de Assertividade** (confluência total) para operar."
                )
                sinal_exibicao = 'SEM SINAL DE ENTRADA'
            
            sinal_cor_fundo = 'var(--neutro-fundo)'
            sinal_cor_borda = 'var(--neutro-borda)'
            
        # --- Fim Lógica de Cor e Animação ---

        ultimo_sinal_hora = ULTIMO_SINAL_REGISTRADO['horario']
        ultimo_sinal_tipo = ULTIMO_SINAL_REGISTRADO['sinal_tipo']

        if ultimo_sinal_tipo == 'COMPRA':
            ultimo_sinal_cor_css = 'var(--compra-borda)'
            ultimo_sinal_texto = f'✅ Última Entrada Aprovada: COMPRA ({ULTIMO_SINAL["ativo"]} @ {ULTIMO_SINAL["preco_entrada"]:.5f})'
        elif ultimo_sinal_tipo == 'VENDA':
            ultimo_sinal_cor_css = 'var(--venda-borda)'
            ultimo_sinal_texto = f'❌ Última Entrada Aprovada: VENDA ({ULTIMO_SINAL["ativo"]} @ {ULTIMO_SINAL["preco_entrada"]:.5f})'
        else:
            ultimo_sinal_cor_css = 'var(--neutro-borda)'
            ultimo_sinal_texto = '🟡 Nenhuma Entrada Aprovada (Aguardando Confluência Total)'

        # Prepara detalhes do sinal
        if ULTIMO_SINAL['assertividade'] > 0:
            signal_details_html = f"""
                <div class="data-item">Preço de Entrada: <strong>{ULTIMO_SINAL['preco_entrada']:.5f}</strong></div>
            """
            analise_detail_html = f"""
                <div class="data-item">Horário da Análise: <strong>{horario_exibicao}</strong></div>
                <div class="assertividade-score">ASSERTIVIDADE DO SINAL: <span style="font-size:1.5em; font-weight:700;">{ULTIMO_SINAL['assertividade']:.0f}%</span></div>
            """
        else:
            signal_details_html = ""
            analise_detail_html = f"""
                <div class="data-item">Horário da Última Análise: <strong>{horario_exibicao}</strong></div>
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
        data_payl
