# main.py
# ROBÔ TRADER M1 (WEB) - ESTRATÉGIA AVANÇADA FINAL
# Inclui SL/TP, Price Action, Suportes/Resistências, Bollinger, RSI, assertividade real

from flask import Flask, Response
import requests
import time
from datetime import datetime
import pytz
from threading import Thread, Lock
import copy
import traceback
import json
import numpy as np

# ====================== CONFIGURAÇÕES ======================
TIMEZONE_BR = 'America/Sao_Paulo'
ATIVOS_MONITORADOS = ['BTC-USDT', 'ETH-USDT', 'EUR-USDT']
API_BASE_URL = 'https://api.kucoin.com/api/v1/market/candles'
INTERVALO_M1 = '1min'
INTERVALO_M5 = '5min'
NUM_VELAS_ANALISE = 20
SCORE_MINIMO_SINAL = 2.0
MAX_HISTORICO = 10
PERCENTUAL_SL_TP = 0.0005
DASHBOARD_REFRESH_RATE_SECONDS = 5
URL_ALERTE_SONORO = "https://www.soundhelix.com/examples/audio/Wave-beep.wav"

# ====================== INICIALIZAÇÃO DO FLASK ======================
app = Flask(__name__)
state_lock = Lock()

# ====================== VARIÁVEIS GLOBAIS ======================
def get_horario_brasilia():
    fuso_brasil = pytz.timezone(TIMEZONE_BR)
    return datetime.now(fuso_brasil)

ULTIMO_SINAL = {
    'horario': get_horario_brasilia().strftime('%H:%M:%S'),
    'ativo': 'N/A',
    'sinal': 'NEUTRO 🟡',
    'score': 0,
    'preco_entrada': 0.0,
    'assertividade': 0,
    'entrada_aprovada': 'Não'
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
        wins = sum(1 for item in HISTORICO_SINAIS if 'WIN' in item['resultado'])
        total = len(HISTORICO_SINAIS)
        losses = total - wins
        percentual = f"{(wins / total) * 100:.2f}%" if total else 'N/A'
        return {'total': total, 'wins': wins, 'losses': losses, 'percentual': percentual}

def get_velas_kucoin(ativo, intervalo):
    try:
        params = {'symbol': ativo, 'type': intervalo}
        r = requests.get(API_BASE_URL, params=params, timeout=8)
        r.raise_for_status()
        data = r.json().get('data', [])
        velas = []
        for v in data:
            velas.append([float(v[1]), float(v[2]), float(v[3]), float(v[4])])
        if intervalo == INTERVALO_M1:
            return velas[-(NUM_VELAS_ANALISE + 1):]
        elif intervalo == INTERVALO_M5:
            return velas[-2:]
    except Exception as e:
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Erro ao obter velas {intervalo} de {ativo}: {e}")
        return []
    return []

def get_tendencia_m5(ativo):
    velas_m5 = get_velas_kucoin(ativo, INTERVALO_M5)
    if len(velas_m5) < 2:
        return 'NEUTRO'
    o_m5, c_m5 = velas_m5[-2][0], velas_m5[-2][1]
    if c_m5 > o_m5:
        return 'UP'
    elif c_m5 < o_m5:
        return 'DOWN'
    else:
        return 'NEUTRO'

# ====================== ANÁLISE AVANÇADA ======================
def calcular_suporte_resistencia(velas):
    suportes, resistencias = [], []
    if len(velas) < 3:
        return suportes, resistencias
    for i in range(1, len(velas)-1):
        o, c, h, l = velas[i]
        o_prev, c_prev, h_prev, l_prev = velas[i-1]
        o_next, c_next, h_next, l_next = velas[i+1]
        if l < l_prev and l < l_next:
            suportes.append(l)
        if h > h_prev and h > h_next:
            resistencias.append(h)
    return suportes, resistencias

def calcular_bollinger(velas, periodo=14):
    closes = np.array([v[1] for v in velas])
    if len(closes) < periodo:
        return None, None, None
    sma = np.mean(closes[-periodo:])
    std = np.std(closes[-periodo:])
    upper = sma + (2 * std)
    lower = sma - (2 * std)
    return upper, sma, lower

def calcular_rsi(velas, periodo=14):
    closes = np.array([v[1] for v in velas])
    if len(closes) < periodo + 1:
        return None
    deltas = np.diff(closes[-(periodo+1):])
    ganhos = deltas[deltas > 0].sum() / periodo
    perdas = -deltas[deltas < 0].sum() / periodo
    if perdas == 0:
        return 100
    rs = ganhos / perdas
    rsi = 100 - (100 / (1 + rs))
    return rsi

def analisar_estrategia_avancada(velas_m1):
    if len(velas_m1) < 14:
        return {
            'sinal': 'NEUTRO 🟡',
            'score': 0,
            'preco_entrada': velas_m1[-1][1] if velas_m1 else 0.0,
            'assertividade': 0,
            'entrada_aprovada': 'Não'
        }

    ultimo_close = velas_m1[-1][1]
    suportes, resistencias = calcular_suporte_resistencia(velas_m1)
    upper_bb, middle_bb, lower_bb = calcular_bollinger(velas_m1)
    rsi = calcular_rsi(velas_m1)

    score = 0
    acao = 'NEUTRO 🟡'

    # Bollinger + candle de reversão
    o, c, h, l = velas_m1[-1]
    if lower_bb and c <= lower_bb:
        score += 1
    if upper_bb and c >= upper_bb:
        score -= 1

    # RSI
    if rsi and rsi < 30:
        score += 1
    if rsi and rsi > 70:
        score -= 1

    # Suporte/Resistência
    if suportes and ultimo_close <= min(suportes):
        score += 1
    if resistencias and ultimo_close >= max(resistencias):
        score -= 1

    # Determina sinal
    if score >= 3:
        acao = 'COMPRA'
    elif score <= -3:
        acao = 'VENDA'

    # Calcula assertividade (%)
    assertividade = min(abs(score) / 4 * 100, 100)

    entrada_aprovada = 'Sim' if assertividade >= 80 else 'Não'
    if entrada_aprovada == 'Não':
        acao = 'NEUTRO 🟡'

    return {
        'sinal': acao,
        'score': score,
        'preco_entrada': ultimo_close,
        'assertividade': assertividade,
        'entrada_aprovada': entrada_aprovada
    }

# ====================== CHECAGEM SL/TP DETALHADA ======================
def checar_resultado_sinal(sinal_checar):
    global HISTORICO_SINAIS
    try:
        ativo = sinal_checar['ativo']
        preco_entrada = sinal_checar['preco_entrada']
        direcao_sinal = sinal_checar['sinal']
        if ativo == 'N/A' or 'NEUTRO' in direcao_sinal or 'Filtrado' in direcao_sinal:
            return

        velas_exp = get_velas_kucoin(ativo, INTERVALO_M1)
        if len(velas_exp) < 1:
            return

        o_exp, c_exp, h_exp, l_exp = velas_exp[-1] 
        resultado = 'NEUTRO'
        percentual_sl_tp = PERCENTUAL_SL_TP

        if 'COMPRA' in direcao_sinal:
            tp
