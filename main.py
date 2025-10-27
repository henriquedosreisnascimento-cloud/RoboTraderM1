"""
ROBÔ TRADER M1 (WEB) - VERSÃO COMPLETA COM CAIXA DE AVISO VISUAL
    - **NOVO:** Adição da caixa visual de "Último Sinal Registrado" (pequena e colorida).
    - Mantém TODAS as informações anteriores (Relógio, Sinal, Assertividade, Histórico, Explicação).
"""

from flask import Flask, json
import requests
import time
from datetime import datetime
import pytz
from threading import Thread
import os 
import copy 

# ====================== CORREÇÃO DO ERRO DE INICIALIZAÇÃO ======================
app = Flask(__name__) 

# ====================== CONFIGURAÇÕES ======================
TIMEZONE_BR = 'America/Sao_Paulo'
ATIVOS_MONITORADOS = ['BTC-USDT', 'ETH-USDT', 'EUR-USDT'] 
API_BASE_URL = 'https://api.kucoin.com/api/v1/market/candles'
INTERVALO = '1min'
NUM_VELAS_ANALISE = 3 
SCORE_MINIMO_SINAL = 2.0 
MAX_HISTORICO = 10 

# URL DO SOM DE ALERTA: Apito (mantido, mas sem o botão de desbloqueio)
