# ROBÔ TRADER M1 (WEB) - VERSÃO COMPLETA COM INTERFACE
# CORREÇÃO DEFINITIVA: Escapando todas as chaves literais {{}} no bloco HTML.

from flask import Flask, json
import requests
import time
from datetime import datetime
import pytz
from threading import Thread
import os 
import copy 

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

# URL DO SOM DE ALERTA
URL_ALERTE_SONORO = "https://www.soundhelix.com/examples/audio/Wave-beep.wav"

# ====================== VARIÁVEIS GLOBAIS DE ESTADO ======================
def get_horario_brasilia():
    fuso_brasil = pytz.timezone(TIMEZONE_BR)
    return datetime.now(fuso_brasil)

# Inicializa as variáveis globais que serão usadas por todas as threads

ULTIMO_SINAL = {
    'horario': get_horario_brasilia().strftime('%H:%M:%S'),
    'ativo': 'N/A', 
    'sinal': 'NEUTRO 🟡', 
    'score': 0, 
    'preco_entrada': 0.0
}

# Variável para a caixa visual do último sinal FORTE
ULTIMO_SINAL_REGISTRADO = {
    'horario': 'N/A',
    'sinal_tipo': 'N/A'
}

HISTORICO_SINAIS = [] 
ULTIMO_SINAL_CHECAR = None 

# ====================== FUNÇÕES BASE ======================
def calcular_assertividade():
    """Calcula a assertividade (Wins e Losses) baseada no histórico."""
    if not HISTORICO_SINAIS:
        return {'total': 0, 'wins': 0, 'losses': 0, 'percentual': 'N/A'}

    wins = sum(1 for item in HISTORICO_SINAIS if item['resultado'] == 'WIN ✅')
    total = len(HISTORICO_SINAIS)
    losses = total - wins
    percentual = f"{(wins / total) * 100:.2f}%"

    return {'total': total, 'wins': wins, 'losses': losses, 'percentual': 'N/A' if total == 0 else percentual}

def get_ultimas_velas(ativo):
    """Busca as últimas velas da Kucoin para o ativo especificado."""
    try:
        params = {'symbol': ativo, 'type': INTERVALO} 
        r = requests.get(API_BASE_URL, params=params, timeout=5)
        r.raise_for_status() 
        data = r.json().get('data', [])

        velas = []
        for v in data[-NUM_VELAS_ANALISE - 1:]: 
            # v[1] = open, v[3] = close, v[4] = high, v[2] = low
            velas.append([float(v[1]), float(v[3]), float(v[4]), float(v[2])]) 

        return velas
    except Exception as e:
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Erro ao obter velas de {ativo}: {e}")
        return []

def analisar_price_action(velas):
    """Analisa as últimas duas velas para gerar um score de sinal."""
    if len(velas) < 2: return {'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}

    o1, c1 = velas[-1][0], velas[-1][3] 
    o2, c2 = velas[-2][0], velas[-2][3] 

    score = 0
    # Vela 1
    if c1 > o1: score += 1
    elif c1 < o1: score -= 1
    # Vela 2
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

    # c1 é o preço de fechamento da vela mais recente, usado como preço de entrada
    return {'sinal': sinal_emoji, 'score': score, 'preco_entrada': c1}

def checar_resultado_sinal(sinal_checar):
    """Checa o resultado do sinal na vela seguinte e atualiza o histórico."""
    global HISTORICO_SINAIS

    ativo = sinal_checar['ativo']
    preco_entrada = sinal_checar['preco_entrada']
    direcao_sinal = sinal_checar['sinal']

    if ativo == 'N/A' or 'NEUTRO' in direcao_sinal:
        return

    # Pede mais uma vela para checar o resultado (a vela de expiração)
    velas = get_ultimas_velas(ativo) 

    if len(velas) < 1:
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Sem dados para checar resultado de {ativo}.")
        return

    # VELA DE EXPIRAÇÃO (a mais recente)
    c_exp = velas[-1][3]

    resultado = 'NEUTRO'

    if 'COMPRA' in direcao_sinal:
        # É WIN se o preço de expiração for maior que o preço de entrada
        resultado = 'WIN ✅' if c_exp > preco_entrada else 'LOSS ❌'
    elif 'VENDA' in direcao_sinal:
        # É WIN se o preço de expiração for menor que o preço de entrada
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

def ciclo_analise():
    """Loop principal que roda a cada 60 segundos para gerar e checar sinais."""
    global ULTIMO_SINAL, ULTIMO_SINAL_CHECAR, ULTIMO_SINAL_REGISTRADO
    
    # Inicia a checagem com um sleep para não rodar imediatamente no momento da inicialização
    time.sleep(1)

    while True:
        horario_atual_dt = get_horario_brasilia()
        horario_atual_str = horario_atual_dt.strftime('%H:%M:%S')

        # 1. Checa o resultado do sinal anterior (se houver um sinal forte para checar)
        if ULTIMO_SINAL_CHECAR:
            checar_resultado_sinal(ULTIMO_SINAL_CHECAR)
            ULTIMO_SINAL_CHECAR = None

        print(f"[{horario_atual_str}] Iniciando novo ciclo de análise...")

        melhor = {'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}

        # 2. Itera por todos os ativos para encontrar o melhor sinal
        for ativo in ATIVOS_MONITORADOS:
            velas = get_ultimas_velas(ativo)
            analise = analisar_price_action(velas)

            # Prioriza o sinal com maior score absoluto
            if abs(analise['score']) >= abs(melhor['score']):
                melhor = {'ativo': ativo, **analise}

        # 3. FILTRAGEM DE SINAIS: Só permite sinais FORTES (Score 2 ou -2)
        if abs(melhor['score']) == SCORE_MINIMO_SINAL:
            sinal_final = melhor
            sinal_final['horario'] = horario_atual_str # Adiciona o horário
        else:
            sinal_final = {
                'horario': horario_atual_str,
                'ativo': 'N/A',
                'sinal': 'NEUTRO 🟡',
                'score': 0,
                'preco_entrada': 0.0
            }

        # 4. Atualiza o registro do último sinal FORTE (para a caixa visual e checagem de resultado)
        if abs(sinal_final['score']) == SCORE_MINIMO_SINAL:
            # Deepcopy é essencial para passar o objeto por valor, evitando que a checagem o altere.
            ULTIMO_SINAL_CHECAR = copy.deepcopy(sinal_final) 
            ULTIMO_SINAL_REGISTRADO = {
                'horario': sinal_final['horario'],
                'sinal_tipo': 'COMPRA' if 'COMPRA' in sinal_final['sinal'] else 'VENDA'
            }
        
        # 5. Atualiza o sinal atual (mesmo que neutro)
        ULTIMO_SINAL.update({
            'horario': sinal_final['horario'],
            'ativo': sinal_final['ativo'],
            'sinal': sinal_final['sinal'],
            'score': sinal_final['score'],
            'preco_entrada': sinal_final['preco_entrada']
        })

        print(f"[{horario_atual_str}] 📢 Novo Sinal: {ULTIMO_SINAL['ativo']} - {ULTIMO_SINAL['sinal']} (Score: {ULTIMO_SINAL['score']})")

        # Aguarda 60 segundos antes da próxima análise
        time.sleep(60)

# ====================== FUNÇÃO AUXILIAR PARA HISTÓRICO DE SINAIS ======================
def formatar_historico_html(historico):
    """
    Formata o histórico de sinais em uma string HTML segura,
    isolando a lógica de formatação do f-string principal.
    """
    linhas_html = []
    # Itera de trás para frente para mostrar o mais recente primeiro
    for item in reversed(historico):
        # Determina a classe CSS com base no resultado
        classe = 'win' if 'WIN' in item['resultado'] else 'loss'
        
        # Cria a linha formatada. Usando aspas duplas fora, e simples dentro, é mais seguro.
        # Aqui, estamos usando chaves normais pois estamos fora do bloco triplo-aspado do HTML
        linha = (
            f"[{item['horario']}] {item['ativo']} -> "
            f"<span class='{classe}'>{item['resultado']}</span> "
            f"(Sinal: {item['sinal']})"
        )
        linhas_html.append(linha)
        
    return '<br>'.join(linhas_html)


# ====================== SERVIDOR HTTPS (ENDPOINT) - INTERFACE COMPLETA + AVISO ======================
@app.route('/')
def home():
    """Endpoint que retorna HTML com layout completo e a nova caixa de aviso."""

    assertividade_data = calcular_assertividade()

    # Horário atual de Brasília no momento da requisição (ATUALIZA A CADA 5 SEGUNDOS)
    horario_atual_brasilia = get_horario_brasilia().strftime('%H:%M:%S') 

    # ====================== LÓGICA PARA MENSAGEM CLARA E EXIBIÇÃO ======================
    sinal_exibicao = ULTIMO_SINAL['sinal']
    ativo_exibicao = f"em {ULTIMO_SINAL['ativo']}"
    horario_exibicao = ULTIMO_SINAL['horario']

    # LÓGICA DA EXPLICAÇÃO
    explicacao = ""
    if 'COMPRA FORTE' in ULTIMO_SINAL['sinal']:
        explicacao = (
            f"Entrada de <strong>COMPRA FORTE</strong> no ativo <strong>{ULTIMO_SINAL['ativo']}</strong>."
            f"<br>Estratégia: O preço demonstrou força de alta por <strong>duas ou mais velas M1 consecutivas</strong>, indicando uma forte tendência de continuação no próximo minuto (Score: {ULTIMO_SINAL['score']})."
        )
    elif 'VENDA FORTE' in ULTIMO_SINAL['sinal']:
        explicacao = (
            f"Entrada de <strong>VENDA FORTE</strong> no ativo <strong>{ULTIMO_SINAL['ativo']}</strong>."
            f"<br>Estratégia: O preço demonstrou força de baixa por <strong>duas ou mais velas M1 consecutivas</strong>, indicando uma forte tendência de continuação no próximo minuto (Score: {ULTIMO_SINAL['score']})."
        )
    else:
        sinal_exibicao = 'SEM SINAL DE ENTRADA'
        ativo_exibicao = 'AGUARDANDO CONFLUÊNCIA'
        horario_exibicao = ULTIMO_SINAL['horario']
        explicacao = (
            "No momento, o robô está em <strong>NEUTRO</strong>. Nenhuma das moedas monitoradas atingiu o score mínimo (Score 2 ou -2) para uma entrada de alta confiança."
            "<br>Estratégia: Aguardando a formação de <strong>duas ou mais velas M1 consecutivas</strong> na mesma direção forte."
        )

    # Cores de Fundo (Strings com nomes de variáveis CSS)
    sinal_cor_fundo = 'var(--neutro-fundo)'
    sinal_cor_borda = 'var(--neutro-borda)'
    sinal_classe_animacao = ''
    alerta_js = "" 

    if 'FORTE 🚀' in ULTIMO_SINAL['sinal']:
        sinal_cor_fundo = 'var(--compra-fundo)' 
        sinal_cor_borda = 'var(--compra-borda)' 
        sinal_classe_animacao = 'signal-active' 
        # Adiciona a lógica de áudio no JS (variáveis Python fora do f-string)
        alerta_js = f"""
            var audio = document.getElementById('alertaAudio');
            audio.currentTime = 0; 
            audio.volume = 0.8; 
            audio.play().catch(e => console.log("Áudio bloqueado pelo navegador."));
        """
    elif 'FORTE 📉' in ULTIMO_SINAL['sinal']:
        sinal_cor_fundo = 'var(--venda-fundo)' 
        sinal_cor_borda = 'var(--venda-borda)' 
        sinal_classe_animacao = 'signal-active' 
        # Adiciona a lógica de áudio no JS (variáveis Python fora do f-string)
        alerta_js = f"""
            var audio = document.getElementById('alertaAudio');
            audio.currentTime = 0; 
            audio.volume = 0.8; 
            audio.play().catch(e => console.log("Áudio bloqueado pelo navegador."));
        """

    # Detalhes do Último Sinal Registrado (para a caixa pequena)
    ultimo_sinal_hora = ULTIMO_SINAL_REGISTRADO['horario']
    ultimo_sinal_tipo = ULTIMO_SINAL_REGISTRADO['sinal_tipo']

    # Cores e texto para a Caixa de Último Sinal
    if ultimo_sinal_tipo == 'COMPRA':
        # Usando a variável CSS diretamente para evitar problemas de aspas
        ultimo_sinal_cor_css = 'var(--compra-borda)' 
        ultimo_sinal_texto = f'✅ Última Entrada: COMPRA (Horário: {ultimo_sinal_hora})'
    elif ultimo_sinal_tipo == 'VENDA':
        # Usando a variável CSS diretamente para evitar problemas de aspas
        ultimo_sinal_cor_css = 'var(--venda-borda)'
        ultimo_sinal_texto = f'❌ Última Entrada: VENDA (Horário: {ultimo_sinal_hora})'
    else:
        ultimo_sinal_cor_css = 'var(--neutro-borda)'
        ultimo_sinal_texto = '🟡 Nenhuma Entrada Forte Registrada'
        
    # 1. Pré-calcula o HTML dos detalhes do sinal ativo
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
    
    # 2. Pré-calcula o HTML do Histórico
    historico_html = formatar_historico_html(HISTORICO_SINAIS)
    
    # === Bloco de CSS Estático - Escapando todas as chaves literais com {{ }} ===
    # Apenas as variáveis Python serão interpoladas. As chaves do CSS ficam intactas.
    css_content = f"""
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
    h1 {{ 
        color: var(--accent-blue); 
        border-bottom: 1px solid var(--neutro-borda); 
        padding-bottom: 15px; 
        margin-bottom: 25px; 
        text-align: center; 
        font-weight: 600;
        font-size: 1.8em; 
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
        font-size: 2.0em; 
        font-weight: 700;
        color: #FFFFFF;
        line-height: 1.1;
    }}

    /* Novo Box de Aviso de Último Sinal */
    .last-signal-box {{
        background-color: #3B3F50;
        border: 1px solid #4D5970;
        border-left: 5px solid {ultimo_sinal_cor_css}; 
        padding: 10px 15px;
        border-radius: 8px;
        margin-bottom: 20px;
        font-size: 1.0em;
        font-weight: 500;
        color: var(--text-primary);
        text-align: center;
        box-shadow: 0 3px 10px rgba(0, 0, 0, 0.4);
    }}


    /* Layout Principal */
    .main-content-grid {{ 
        display: flex; 
        gap: 15px; 
        margin-bottom: 25px; 
        flex-direction: column; 
    }}
    @media (min-width: 768px) {{
        .main-content-grid {{
            flex-direction: row; 
        }}
    }}
    .sinal-box, .assertividade-box {{ 
        flex: 1; 
        padding: 20px; 
        border-radius: 15px; 
        transition: all 0.5s ease-in-out;
        box-shadow: 0 5px 15px rgba(0, 0, 0, 0.3);
    }}

    /* Estilo da Caixa de Sinal - Uso de variáveis Python para cor */
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
    .assertividade-box p {{ margin: 0; padding: 3px 0; font-size: 1.0em; font-weight: 400;}}
    .assertividade-box span {{ font-weight: 700; color: var(--assert-borda); font-size: 2.5em; line-height: 1.1; margin: 5px 0; }}

    /* Histórico */
    h2 {{ color: var(--accent-blue); font-weight: 600; margin-bottom: 10px; font-size: 1.5em; }}
    pre {{ background-color: #101520; padding: 15px; border-radius: 12px; overflow: auto; color: #B0B0B0; font-size: 0.85em; }}
    .win {{ color: var(--compra-borda); font-weight: 700; }}
    .loss {{ color: var(--venda-borda); font-weight: 700; }}

    /* Mensagem de Aviso (Áudio) */
    .warning-message {{
        background-color: #FFC10720;
        color: #FFC107;
        padding: 8px;
        border-radius: 8px;
        text-align: center;
        margin-bottom: 15px;
        font-weight: 500;
        border: 1px solid #FFC107;
        font-size: 0.9em;
    }}

    /* Caixa de Informação/Explicação */
    .info-box {{
        margin-top: 25px;
        padding: 15px;
        background-color: #30394c; 
        border-left: 5px solid var(--accent-blue);
        border-radius: 8px;
        font-size: 0.95em;
        line-height: 1.6;
        color: #B0B9CC;
    }}
    .info-box strong {{
        color: var(--text-primary);
        font-weight: 600;
    }}
    """

    # HTML com CSS e o elemento de Áudio
    # Usando o f-string triplo-aspado principal para o HTML
    html_content = f"""
    <!DOCTYPE
    html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="refresh" content="5"> 
        <title>ROBÔ TRADER M1 - Dashboard Completo</title>

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
                <p style="margin-bottom: 0px;">HORÁRIO ATUAL DE BRASÍLIA</p>
                <div class="current-time">{horario_atual_brasilia}</div>
            </div>

            <div class="warning-message">
                ⚠️ Aviso: O apito de entrada está configurado, mas o navegador pode bloqueá-lo. Clique na tela para liberar 
