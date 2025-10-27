"""
ROBÔ TRADER M1 (WEB) - VERSÃO COMPLETA COM CAIXA DE AVISO VISUAL
    - **CORREÇÃO:** Erro de sintaxe da Thread corrigido.
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
import logging

# Configuração de Logging para melhor visibilidade no Render
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Variáveis de ambiente para porta (necessário para Render)
PORT = int(os.environ.get('PORT', 5000))

# ====================== INICIALIZAÇÃO DO FLASK ======================
app = Flask(__name__) 

# ====================== CONFIGURAÇÕES ======================
TIMEZONE_BR = 'America/Sao_Paulo'
ATIVOS_MONITORADOS = ['BTC-USDT', 'ETH-USDT', 'EUR-USDT'] 
API_BASE_URL = 'https://api.kucoin.com/api/v1/market/candles'
INTERVALO = '1min'
NUM_VELAS_ANALISE = 3 
SCORE_MINIMO_SINAL = 2.0 
MAX_HISTORICO = 10 

# URL DO SOM DE ALERTA: Apito 
URL_ALERTE_SONORO = "https://www.soundhelix.com/examples/audio/Wave-beep.wav"

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

# Variável para a caixa visual (último sinal que realmente foi dado)
ULTIMO_SINAL_REGISTRADO = {
    'horario': 'N/A',
    'sinal_tipo': 'N/A' # COMPRA, VENDA, N/A
}

HISTORICO_SINAIS = [] 
ULTIMO_SINAL_CHECAR = None # Armazena a referência para o sinal que será checado no próximo ciclo

# ====================== FUNÇÕES BASE ======================
def calcular_assertividade():
    """Calcula as métricas de WINS, LOSSES e Percentual de assertividade."""
    if not HISTORICO_SINAIS:
        return {'total': 0, 'wins': 0, 'losses': 0, 'percentual': 'N/A'}

    wins = sum(1 for item in HISTORICO_SINAIS if item['resultado'] == 'WIN ✅')
    total = len(HISTORICO_SINAIS)
    losses = total - wins
    percentual = f"{(wins / total) * 100:.2f}%"

    return {'total': total, 'wins': wins, 'losses': losses, 'percentual': percentual}

def get_ultimas_velas(ativo):
    """Busca as velas do Kucoin (simulação de dados de mercado)."""
    try:
        params = {'symbol': ativo, 'type': INTERVALO} 
        r = requests.get(API_BASE_URL, params=params, timeout=5)
        r.raise_for_status() 
        data = r.json().get('data', [])

        velas = []
        # Obtém as N últimas velas necessárias para análise e a vela atual
        for v in data[-NUM_VELAS_ANALISE - 1:]: 
            # v[1] = open, v[3] = close, v[4] = high, v[2] = low
            velas.append([float(v[1]), float(v[3]), float(v[4]), float(v[2])]) 

        return velas
    except Exception as e:
        logging.warning(f"⚠️ Erro ao obter velas de {ativo}: {e}")
        return []

def analisar_price_action(velas):
    """Analisa o price action das últimas velas e atribui um score."""
    # Precisa de pelo menos 2 velas (a atual e a anterior fechada)
    if len(velas) < NUM_VELAS_ANALISE: 
        return {'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}

    # Analisa as velas fechadas (todas, exceto a última que é a atual)
    score = 0
    # O loop começa da segunda vela mais antiga até a mais recente fechada
    for i in range(1, NUM_VELAS_ANALISE + 1):
        o, c = velas[-i][0], velas[-i][3]
        if c > o: score += 1 # Vela de alta
        elif c < o: score -= 1 # Vela de baixa

    # O preço de entrada é o fechamento da vela anterior à atual (velas[-2][3])
    # Como a API do Kucoin retorna a vela atual, o preço de entrada deve ser o fechamento
    # da vela que o sinal será dado (a última vela fechada - velas[-2])
    # Ajustando: o preço de entrada é o fechamento da última vela *fechada*
    preco_entrada = velas[-2][3] if len(velas) >= 2 else 0.0


    if score >= SCORE_MINIMO_SINAL:
        sinal_emoji = 'COMPRA FORTE 🚀' 
    elif score <= -SCORE_MINIMO_SINAL:
        sinal_emoji = 'VENDA FORTE 📉' 
    # Sinais fracos não serão usados para entrada, apenas para fins de score.
    elif score > 0:
        sinal_emoji = 'COMPRA Fraca 🟢' 
    elif score < 0:
        sinal_emoji = 'VENDA Fraca 🔴' 
    else:
        sinal_emoji = 'NEUTRO 🟡' 

    return {'sinal': sinal_emoji, 'score': score, 'preco_entrada': preco_entrada}

def checar_resultado_sinal(sinal_checar):
    """Checa o resultado do sinal dado no ciclo anterior."""
    global HISTORICO_SINAIS

    ativo = sinal_checar['ativo']
    preco_entrada = sinal_checar['preco_entrada']
    direcao_sinal = sinal_checar['sinal']

    if ativo == 'N/A' or 'NEUTRO' in direcao_sinal:
        return

    # Obtém as velas novamente. Precisamos da vela de expiração (a que fechou no ciclo atual).
    velas = get_ultimas_velas(ativo) 

    if len(velas) < 2: # Precisa de pelo menos 2 velas para confirmar (a vela atual e a de entrada)
        logging.warning(f"⚠️ Sem dados suficientes para checar resultado de {ativo}.")
        return

    # VELA DE EXPIRAÇÃO (A vela que fechou neste ciclo de 60s)
    # A vela [-2] é a vela de entrada. A vela [-1] é a vela de expiração/resultado.
    # Se o sinal foi dado no final da vela T-1, o resultado é o fechamento da vela T.
    c_exp = velas[-1][3]

    resultado = 'NEUTRO'

    if 'COMPRA' in direcao_sinal:
        resultado = 'WIN ✅' if c_exp > preco_entrada else 'LOSS ❌'
    elif 'VENDA' in direcao_sinal:
        resultado = 'WIN ✅' if c_exp < preco_entrada else 'LOSS ❌'
    
    # Previne duplicação (caso a API retorne resultados diferentes em chamadas diferentes)
    if not any(item['horario'] == sinal_checar['horario'] and item['ativo'] == ativo for item in HISTORICO_SINAIS):
        HISTORICO_SINAIS.append({
            'horario': sinal_checar['horario'],
            'ativo': ativo,
            'sinal': direcao_sinal,
            'resultado': resultado,
            'preco_entrada': round(preco_entrada, 5), # Arredonda para legibilidade
            'preco_expiracao': round(c_exp, 5)
        })

    if len(HISTORICO_SINAIS) > MAX_HISTORICO:
        HISTORICO_SINAIS.pop(0)

    logging.info(f"🎯 Resultado de {ativo} ({sinal_checar['horario']}): {resultado}")

def ciclo_analise():
    """O loop principal do robô que é executado em um Thread separado."""
    global ULTIMO_SINAL, ULTIMO_SINAL_CHECAR, ULTIMO_SINAL_REGISTRADO
    
    # Garante que as funções print e log só mostrem a mensagem de inicialização uma vez
    logging.info("Robô Trader M1 (Thread de Análise) iniciado.")
    
    while True:
        horario_atual = get_horario_brasilia().strftime('%H:%M:%S')

        # 1. Checa o resultado do sinal anterior (se houver)
        if ULTIMO_SINAL_CHECAR:
            checar_resultado_sinal(ULTIMO_SINAL_CHECAR)
            ULTIMO_SINAL_CHECAR = None

        logging.info(f"[{horario_atual}] Iniciando novo ciclo de análise...")

        melhor = {'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}

        # 2. Itera por todos os ativos para encontrar o melhor sinal
        for ativo in ATIVOS_MONITORADOS:
            velas = get_ultimas_velas(ativo)
            analise = analisar_price_action(velas)

            # Prioriza o sinal com maior score absoluto
            if abs(analise['score']) >= abs(melhor['score']):
                melhor = {'ativo': ativo, **analise}

        # 3. FILTRAGEM DE SINAIS: Só permite sinais FORTES (Score 2 ou -2)
        if abs(melhor['score']) >= SCORE_MINIMO_SINAL:
            sinal_final = melhor
            sinal_final['horario'] = get_horario_brasilia().strftime('%H:%M:%S')
        else:
            # Caso não haja sinal forte, reseta o objeto
            sinal_final = {
                'horario': get_horario_brasilia().strftime('%H:%M:%S'),
                'ativo': 'N/A',
                'sinal': 'NEUTRO 🟡',
                'score': 0,
                'preco_entrada': 0.0
            }

        # 4. Atualiza o registro do último sinal FORTE e prepara para checagem
        if abs(sinal_final['score']) >= SCORE_MINIMO_SINAL:
            # Salva uma cópia profunda para evitar que ULTIMO_SINAL mude a referência antes da checagem
            ULTIMO_SINAL_CHECAR = copy.deepcopy(sinal_final) 
            
            ULTIMO_SINAL_REGISTRADO = {
                'horario': sinal_final['horario'],
                'sinal_tipo': 'COMPRA' if 'COMPRA' in sinal_final['sinal'] else 'VENDA'
            }
        
        # 5. Atualiza o sinal que será exibido no dashboard
        ULTIMO_SINAL = sinal_final
        
        logging.info(f"📢 Novo Sinal (Dashboard): {ULTIMO_SINAL['ativo']} - {ULTIMO_SINAL['sinal']} (Score: {ULTIMO_SINAL['score']})")

        # 6. Aguarda 60 segundos antes da próxima execução (M1)
        time.sleep(60)

# ====================== SERVIDOR HTTPS (ENDPOINT) - INTERFACE COMPLETA + AVISO ======================
@app.route('/')
def home():
    """Endpoint que retorna HTML com layout completo e a nova caixa de aviso."""

    assertividade_data = calcular_assertividade()

    # Horário atual de Brasília no momento da requisição
    horario_atual_brasilia = get_horario_brasilia().strftime('%H:%M:%S') 

    # --- LÓGICA DE EXIBIÇÃO ---
    sinal_exibicao = ULTIMO_SINAL['sinal']
    ativo_exibicao = f"em {ULTIMO_SINAL['ativo']}"
    horario_exibicao = ULTIMO_SINAL['horario']

    # ====================== LÓGICA DA EXPLICAÇÃO ======================
    explicacao = ""
    score_abs = abs(ULTIMO_SINAL['score'])
    
    if score_abs >= SCORE_MINIMO_SINAL:
        direcao = 'ALTA' if 'COMPRA' in ULTIMO_SINAL['sinal'] else 'BAIXA'
        explicacao = (
            f"Entrada de <strong>{ULTIMO_SINAL['sinal']}</strong> no ativo <strong>{ULTIMO_SINAL['ativo']}</strong>."
            f"<br>Estratégia: O preço demonstrou força de {direcao} por <strong>{score_abs} ou mais velas M1 consecutivas</strong>, indicando uma forte tendência de continuação no próximo minuto (Score: {score_abs})."
        )
    else:
        sinal_exibicao = 'SEM SINAL DE ENTRADA'
        ativo_exibicao = 'AGUARDANDO CONFLUÊNCIA'
        horario_exibicao = ULTIMO_SINAL['horario']
        explicacao = (
            "No momento, o robô está em <strong>NEUTRO</strong>. Nenhuma das moedas monitoradas atingiu o score mínimo (Score {SCORE_MINIMO_SINAL} ou -{SCORE_MINIMO_SINAL}) para uma entrada de alta confiança."
            "<br>Estratégia: Aguardando a formação de <strong>{SCORE_MINIMO_SINAL} ou mais velas M1 consecutivas</strong> na mesma direção forte."
        )

    # Cores de Fundo (Glassmorphism Suave)
    sinal_cor_fundo = 'var(--neutro-fundo)'
    sinal_cor_borda = 'var(--neutro-borda)'
    sinal_classe_animacao = ''
    alerta_js = "" 

    if 'FORTE 🚀' in ULTIMO_SINAL['sinal']:
        sinal_cor_fundo = 'var(--compra-fundo)' 
        sinal_cor_borda = 'var(--compra-borda)' 
        sinal_classe_animacao = 'signal-active' 
        alerta_js = """
            var audio = document.getElementById('alertaAudio');
            audio.currentTime = 0; 
            audio.volume = 0.8; 
            // O catch é para lidar com a exceção de autoplay bloqueado
            audio.play().catch(e => console.log("Áudio bloqueado ou não liberado pelo usuário."));
        """
    elif 'FORTE 📉' in ULTIMO_SINAL['sinal']:
        sinal_cor_fundo = 'var(--venda-fundo)' 
        sinal_cor_borda = 'var(--venda-borda)' 
        sinal_classe_animacao = 'signal-active' 
        alerta_js = """
            var audio = document.getElementById('alertaAudio');
            audio.currentTime = 0; 
            audio.volume = 0.8; 
            audio.play().catch(e => console.log("Áudio bloqueado ou não liberado pelo usuário."));
        """

    # Detalhes do Último Sinal Registrado (para a caixa pequena)
    ultimo_sinal_hora = ULTIMO_SINAL_REGISTRADO['horario']
    ultimo_sinal_tipo = ULTIMO_SINAL_REGISTRADO['sinal_tipo']

    # Cores e texto para a Caixa de Último Sinal
    if ultimo_sinal_tipo == 'COMPRA':
        ultimo_sinal_cor = 'var(--compra-borda)'
        ultimo_sinal_texto = f'✅ Última Entrada: COMPRA ({ULTIMO_SINAL_CHECAR["ativo"]} @ {ULTIMO_SINAL_CHECAR["horario"]})'
    elif ultimo_sinal_tipo == 'VENDA':
        ultimo_sinal_cor = 'var(--venda-borda)'
        ultimo_sinal_texto = f'❌ Última Entrada: VENDA ({ULTIMO_SINAL_CHECAR["ativo"]} @ {ULTIMO_SINAL_CHECAR["horario"]})'
    else:
        ultimo_sinal_cor = 'var(--neutro-borda)'
        ultimo_sinal_texto = '🟡 Nenhuma Entrada Forte Registrada'

    # HTML com CSS e o elemento de Áudio
    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <!-- Recarrega a página a cada 5 segundos para atualizar os dados de tempo e sinal -->
        <meta http-equiv="refresh" content="5"> 
        <meta name="viewport" content="width=device-width, initial-scale=1.0">

        <title>ROBÔ TRADER M1 - Dashboard Completo</title>

        <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">

        <style>
            /* Paleta de Cores e Estilos */
            :root {{
                --bg-primary: #1C2331; /* Fundo suave */
                --bg-secondary: #2A3346; /* Fundo dos boxes */
                --text-primary: #DCE3F4; /* Texto claro suave */
                --accent-blue: #70A0FF; /* Títulos */
                --neutro-fundo: #374257; /* Fundo Neutro */
                --neutro-borda: #4D5970; /* Borda Neutra */

                --compra-fundo: #2D4C42; /* Verde Trade Escuro */
                --compra-borda: #6AA84F; /* Verde Trade */

                --venda-fundo: #5C3A3A; /* Vermelho Trade Escuro */
                --venda-borda: #E06666; /* Vermelho Trade */

                --assert-fundo: #3B3F50;
                --assert-borda: #FFC107; /* Dourado */
            }}

            body {{ 
                background-color: var(--bg-primary); 
                color: var(--text-primary); 
                font-family: 'Poppins', sans-serif; 
                padding: 15px; /* Mais adequado para mobile */
                transition: background-color 0.5s;
            }}
            .container {{ 
                max-width: 950px; 
                margin: auto; 
                background-color: var(--bg-secondary); 
                padding: 25px; 
                border-radius: 20px; 
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5); 
            }}
            h1 {{ 
                color: var(--accent-blue); 
                border-bottom: 1px solid var(--neutro-borda); 
                padding-bottom: 15px; 
                margin-bottom: 25px; 
                text-align: center; 
                font-weight: 600;
            }}

            /* Box de Horário (Relógio) */
            .time-box {{
                background-color: #3B3F50;
                padding: 15px;
                border-radius: 10px;
                text-align: center;
                margin-bottom: 20px;
                box-shadow: 0 3px 10px rgba(0, 0, 0, 0.4);
            }}
            .current-time {{
                font-size: 2.0em; /* Ajuste para mobile */
                font-weight: 700;
                color: #FFFFFF;
                line-height: 1.1;
            }}

            /* Novo Box de Aviso de Último Sinal */
            .last-signal-box {{
                background-color: #3B3F50;
                border: 1px solid #4D5970;
                border-left: 5px solid {ultimo_sinal_cor}; 
                padding: 12px 15px;
                border-radius: 8px;
                margin-bottom: 20px;
                font-size: 0.9em; /* Ajuste para mobile */
                font-weight: 500;
                color: var(--text-primary);
                text-align: center;
                box-shadow: 0 3px 10px rgba(0, 0, 0, 0.4);
            }}


            /* Layout Principal (Flexbox para Mobile) */
            .main-content-grid {{ 
                display: flex; 
                flex-direction: column; /* Colunas empilham em mobile */
                gap: 15px; 
                margin-bottom: 20px; 
            }}
            .sinal-box, .assertividade-box {{ 
                padding: 20px; 
                border-radius: 15px; 
                transition: all 0.5s ease-in-out;
                box-shadow: 0 5px 15px rgba(0, 0, 0, 0.3);
            }}
            /* Desktop/Tablet view */
            @media (min-width: 768px) {{
                .main-content-grid {{ 
                    flex-direction: row; 
                    gap: 25px; 
                    margin-bottom: 40px; 
                }}
                .sinal-box, .assertividade-box {{
                    flex: 1; 
                }}
            }}

            /* Estilo da Caixa de Sinal */
            .sinal-box {{ 
                background-color: {sinal_cor_fundo}; 
                border: 2px solid {sinal_cor_borda}; 
            }}
            .sinal-header {{ 
                font-size: 1.8em; 
                font-weight: 700; 
                color: {sinal_cor_borda}; 
                margin-bottom: 10px; 
            }}
            .data-item {{ margin-bottom: 8px; font-size: 1.0em; font-weight: 400; }}
            .data-item strong {{ font-weight: 600; color: #FFFFFF; }}

            /* Efeito de Destaque */
            .signal-active {{
                box-shadow: 0 0 20px {sinal_cor_borda};
                transform: translateY(-2px);
            }}

            /* Estilo da Caixa de Assertividade */
            .assertividade-box {{ 
                background-color: var(--assert-fundo); 
                border: 2px solid var(--assert-borda); 
                text-align: center;
                display: flex; 
                flex-direction: column;
                justify-content: center;
            }}
            .assertividade-box p {{ margin: 0; padding: 5px 0; font-size: 1.0em; font-weight: 400;}}
            .assertividade-box span {{ font-weight: 700; color: var(--assert-borda); font-size: 2.5em; line-height: 1.1; margin: 5px 0; }}

            /* Histórico */
            h2 {{ color: var(--accent-blue); font-weight: 600; margin-bottom: 15px; font-size: 1.3em; }}
            pre {{ background-color: #101520; padding: 15px; border-radius: 12px; overflow: auto; color: #B0B0B0; font-size: 0.8em; }}
            .win {{ color: var(--compra-borda); font-weight: 700; }}
            .loss {{ color: var(--venda-borda); font-weight: 700; }}

            /* Mensagem de Aviso (Áudio) */
            .warning-message {{
                background-color: #FFC10720;
                color: #FFC107;
                padding: 10px;
                border-radius: 8px;
                text-align: center;
                margin-bottom: 20px;
                font-weight: 500;
                border: 1px solid #FFC107;
                font-size: 0.9em;
            }}

            /* Caixa de Informação/Explicação */
            .info-box {{
                margin-top: 20px;
                padding: 15px;
                background-color: #30394c; 
                border-left: 5px solid var(--accent-blue);
                border-radius: 8px;
                font-size: 0.9em;
                line-height: 1.6;
                color: #B0B9CC;
            }}
            .info-box strong {{
                color: var(--text-primary);
                font-weight: 600;
            }}
        </style>
    </head>
    <body>
        <!-- Elemento de áudio para o alerta -->
        <audio id="alertaAudio" src="{URL_ALERTE_SONORO}" preload="auto"></audio>

        <div class="container">
            <h1>ROBÔ TRADER M1 | DASHBOARD SNIPER</h1>

            <d
