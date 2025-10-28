# main.py
# ROBÔ TRADER M1 (WEB) - VERSÃO FINAL PARA DEPLOY
# Dashboard colorido + SL/TP simulado + SSE
# Arquivo revisado — pronto para subir no Render / GitHub

from flask import Flask, Response, render_template_string
import requests
import time
import os
import copy
import traceback
import json
from datetime import datetime
import pytz
from threading import Thread, Lock

# ====================== CONFIGURAÇÕES ======================
TIMEZONE_BR = 'America/Sao_Paulo'
ATIVOS_MONITORADOS = ['BTC-USDT', 'ETH-USDT', 'EUR-USDT']  # ajuste conforme desejar
API_BASE_URL = 'https://api.kucoin.com/api/v1/market/candles'
INTERVALO_M1 = '1min'
NUM_VELAS_ANALISE = 30         # número de velas a buscar para análise (quando aplicável)
SCORE_MINIMO_SINAL = 2        # score mínimo para considerar sinal "forte"
MAX_HISTORICO = 50            # máximo de entradas no histórico exibido
PERCENTUAL_SL_TP = 0.0005     # 0.05% SL/TP
DASHBOARD_REFRESH_RATE_SECONDS = 5

# ====================== FLASK E ESTADO GLOBAL ======================
app = Flask(__name__)
state_lock = Lock()

def get_horario_brasilia():
    return datetime.now(pytz.timezone(TIMEZONE_BR))

ULTIMO_SINAL = {
    'horario': get_horario_brasilia().strftime('%H:%M:%S'),
    'ativo': 'N/A',
    'sinal': 'NEUTRO 🟡',
    'score': 0,
    'preco_entrada': 0.0
}

ULTIMO_SINAL_REGISTRADO = {'horario': 'N/A', 'sinal_tipo': 'N/A'}
HISTORICO_SINAIS = []
ULTIMO_SINAL_CHECAR = None

# ====================== FUNÇÕES AUXILIARES ======================
def safe_request_get(url, params=None, timeout=8):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception:
        return None

def get_velas_kucoin(ativo, intervalo=INTERVALO_M1, limit=NUM_VELAS_ANALISE):
    """
    Retorna lista de velas no formato [Open, Close, High, Low].
    Tenta sempre devolver até `limit` velas (ou menos se indisponível).
    """
    try:
        params = {'symbol': ativo, 'type': intervalo, 'limit': limit}
        r = safe_request_get(API_BASE_URL, params=params)
        if not r:
            return []
        data = r.json().get('data', [])
        velas = []
        for v in data:
            # KuCoin: [timestamp, open, close, high, low, volume, turnover] (algumas versões)
            # indices usados: v[1]=open, v[2]=close, v[3]=high, v[4]=low
            try:
                o = float(v[1]); c = float(v[2]); h = float(v[3]); l = float(v[4])
                velas.append([o, c, h, l])
            except Exception:
                continue
        return velas
    except Exception:
        return []

def analisar_price_action(velas):
    """
    Simples análise de price action: usa as 2 últimas velas para montar um score.
    Score range: -2 .. +2
    """
    if not velas or len(velas) < 2:
        return {'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}
    o1, c1 = velas[-1][0], velas[-1][1]
    o2, c2 = velas[-2][0], velas[-2][1]
    score = 0
    score += 1 if c1 > o1 else -1 if c1 < o1 else 0
    score += 1 if c2 > o2 else -1 if c2 < o2 else 0

    if score >= SCORE_MINIMO_SINAL:
        sinal = 'COMPRA FORTE 🚀'
    elif score <= -SCORE_MINIMO_SINAL:
        sinal = 'VENDA FORTE 📉'
    elif score > 0:
        sinal = 'COMPRA Fraca 🟢'
    elif score < 0:
        sinal = 'VENDA Fraca 🔴'
    else:
        sinal = 'NEUTRO 🟡'

    return {'sinal': sinal, 'score': score, 'preco_entrada': c1}

def checar_resultado_sinal(sinal_checar):
    """
    Avalia resultado do sinal na vela de expiração (próxima vela M1) usando SL/TP simulados.
    Salva no HISTORICO_SINAIS.
    """
    global HISTORICO_SINAIS
    try:
        ativo = sinal_checar.get('ativo', 'N/A')
        preco_entrada = sinal_checar.get('preco_entrada', 0.0)
        direcao = sinal_checar.get('sinal', 'NEUTRO 🟡')
        if ativo == 'N/A' or 'NEUTRO' in direcao:
            return

        velas = get_velas_kucoin(ativo, INTERVALO_M1, limit=2)
        if not velas:
            return

        # Usamos a última vela (mais recente) como vela de verificação
        o, c, h, l = velas[-1]
        p = PERCENTUAL_SL_TP
        resultado = 'NEUTRO'
        if 'COMPRA' in direcao:
            tp = preco_entrada * (1 + p)
            sl = preco_entrada * (1 - p)
            if h >= tp:
                resultado = 'WIN ✅ (TP)'
            elif l <= sl:
                resultado = 'LOSS ❌ (SL)'
            else:
                resultado = 'WIN ✅ (Close)' if c > preco_entrada else 'LOSS ❌ (Close)'
        elif 'VENDA' in direcao:
            tp = preco_entrada * (1 - p)
            sl = preco_entrada * (1 + p)
            if l <= tp:
                resultado = 'WIN ✅ (TP)'
            elif h >= sl:
                resultado = 'LOSS ❌ (SL)'
            else:
                resultado = 'WIN ✅ (Close)' if c < preco_entrada else 'LOSS ❌ (Close)'

        with state_lock:
            HISTORICO_SINAIS.append({
                'horario': sinal_checar.get('horario', get_horario_brasilia().strftime('%H:%M:%S')),
                'ativo': ativo,
                'sinal': direcao,
                'resultado': resultado,
                'preco_entrada': preco_entrada,
                'preco_expiracao': c
            })
            # mantém somente MAX_HISTORICO entradas
            if len(HISTORICO_SINAIS) > MAX_HISTORICO:
                HISTORICO_SINAIS.pop(0)

    except Exception:
        traceback.print_exc()

def formatar_historico_html():
    """
    Retorna o HTML do histórico (linhas <div>).
    """
    with state_lock:
        historico = list(reversed(HISTORICO_SINAIS))
    linhas = []
    for item in historico:
        classe = 'win' if 'WIN' in item['resultado'] else 'loss'
        diferenca = item['preco_expiracao'] - item['preco_entrada']
        sinal_diff = "+" if diferenca >= 0 else ""
        linha = f"[{item['horario']}] {item['ativo']} -> <span class='{classe}'>{item['resultado']}</span> (Sinal: {item['sinal']}. Diff: {sinal_diff}{diferenca:.6f})"
        linhas.append(linha)
    if not linhas:
        return "Sem operações registradas ainda."
    return "<br>".join(linhas)

def calcular_assertividade():
    """
    Calcula % de wins no histórico (simples).
    """
    with state_lock:
        if not HISTORICO_SINAIS:
            return {'total': 0, 'wins': 0, 'losses': 0, 'percentual': 'N/A'}
        wins = sum(1 for it in HISTORICO_SINAIS if 'WIN' in it['resultado'])
        total = len(HISTORICO_SINAIS)
        losses = total - wins
        percentual = f"{(wins / total) * 100:.2f}%"
        return {'total': total, 'wins': wins, 'losses': losses, 'percentual': percentual}

# ====================== CICLO DE ANÁLISE (BACKGROUND) ======================
def ciclo_analise():
    global ULTIMO_SINAL, ULTIMO_SINAL_CHECAR, ULTIMO_SINAL_REGISTRADO
    time.sleep(1)
    while True:
        try:
            now = get_horario_brasilia()
            # alinha ao próximo minuto
            seconds_until_next_minute = 60 - now.second
            if seconds_until_next_minute <= 0:
                seconds_until_next_minute = 60

            # se houver sinal a checar, faz primeira (gera histórico)
            if ULTIMO_SINAL_CHECAR:
                try:
                    checar_resultado_sinal(ULTIMO_SINAL_CHECAR)
                finally:
                    with state_lock:
                        ULTIMO_SINAL_CHECAR = None

            # coleta sinais simples de todos ativos
            melhores = []
            for ativo in ATIVOS_MONITORADOS:
                velas = get_velas_kucoin(ativo, INTERVALO_M1, limit=NUM_VELAS_ANALISE)
                analise = analisar_price_action(velas)
                analise['ativo'] = ativo
                melhores.append(analise)

            # escolhe o sinal com maior |score|
            melhor = {'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}
            for s in melhores:
                if abs(s.get('score', 0)) >= abs(melhor.get('score', 0)):
                    melhor = s

            horario_str = get_horario_brasilia().strftime('%H:%M:%S')
            sinal_final = {
                'horario': horario_str,
                'ativo': melhor.get('ativo', 'N/A'),
                'sinal': melhor.get('sinal', 'NEUTRO 🟡'),
                'score': melhor.get('score', 0),
                'preco_entrada': float(melhor.get('preco_entrada', 0.0) or 0.0)
            }

            with state_lock:
                # se for sinal forte, marcar para checagem (gera histórico na próxima iteração)
                if abs(sinal_final['score']) >= SCORE_MINIMO_SINAL and 'NEUTRO' not in sinal_final['sinal']:
                    ULTIMO_SINAL_CHECAR = copy.deepcopy(sinal_final)
                    ULTIMO_SINAL_REGISTRADO.update({
                        'horario': sinal_final['horario'],
                        'sinal_tipo': 'COMPRA' if 'COMPRA' in sinal_final['sinal'] else 'VENDA'
                    })
                ULTIMO_SINAL.update(sinal_final)

            print(f"[{horario_str}] Novo sinal: {ULTIMO_SINAL['ativo']} - {ULTIMO_SINAL['sinal']} (Score: {ULTIMO_SINAL['score']})")

        except Exception:
            traceback.print_exc()

        time.sleep(seconds_until_next_minute)

# inicia thread de análise
analysis_thread = Thread(target=ciclo_analise, daemon=True)
analysis_thread.start()

# ====================== DASHBOARD (HTML) ======================
HTML_TEMPLATE = """
<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <title>Robô Trader M1 - Dashboard</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root{
      --bg:#0b1220; --card:#0f1724; --muted:#94a3b8;
      --compra-bg:#09311a; --compra-border:#1bd760;
      --venda-bg:#351515; --venda-border:#ff5b5b;
      --neutro-bg:#373737; --neutro-border:#e3d44a;
      --accent:#70a0ff;
    }
    body{margin:0;font-family:Inter,system-ui,Arial;background:linear-gradient(180deg,#071021 0%, #071827 100%);color:#e6eef8;}
    .wrap{max-width:1000px;margin:28px auto;padding:18px;}
    header{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
    h1{font-size:1.25rem;margin:0;color:var(--accent)}
    .time{color:var(--muted);font-size:0.95rem}
    .grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
    .card{background:var(--card);padding:16px;border-radius:12px;box-shadow:0 8px 30px rgba(2,6,23,0.6)}
    .signal{display:flex;flex-direction:column;align-items:center;justify-content:center;height:140px;border-radius:10px;padding:12px;border:2px solid transparent;transition:all .25s}
    .signal strong{display:block;font-size:1.1rem;margin-bottom:6px}
    .info-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
    .pill{background:rgba(255,255,255,0.03);padding:6px 10px;border-radius:999px;font-size:0.9rem;border:1px solid rgba(255,255,255,0.03)}
    .historico-list{max-height:280px;overflow:auto;padding-right:6px}
    .win{color:#7ef59a;font-weight:600}
    .loss{color:#ff958b;font-weight:600}
    .muted{color:var(--muted)}
    footer{margin-top:16px;color:var(--muted);font-size:0.85rem;text-align:center}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>🚀 Robô Trader M1 - Dashboard</h1>
      <div class="time" id="current-time">--:--:--</div>
    </header>

    <div class="grid">
      <div class="card">
        <div id="signal-box" class="signal" style="background:var(--neutro-bg);border-color:var(--neutro-border)">
          <strong id="sinal-text">Carregando...</strong>
          <div id="ativo-text" class="muted">Ativo: N/A</div>
          <div class="info-row">
            <div class="pill" id="score-pill">Score: 0</div>
            <div class="pill" id="preco-pill">Preço: 0.00000</div>
            <div class="pill" id="assert-pill">Assert: N/A</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div style="display:flex;flex-direction:column;gap:8px">
          <div style="font-weight:700">Últimos Trades / Histórico</div>
          <div class="historico-list" id="historico"></div>
        </div>
      </div>
    </div>

    <footer>Atualiza a cada <strong id="refresh-seconds">5</strong> segundos — Sinais: verde=compra, vermelho=venda, amarelo=neutro</footer>
  </div>

  <script>
    const evt = new EventSource('/stream');
    evt.onmessage = function(e){
      try {
        const d = JSON.parse(e.data);
        document.getElementById('current-time').innerText = (new Date()).toLocaleTimeString();
        const sigBox = document.getElementById('signal-box');
        const sinalText = document.getElementById('sinal-text');
        const ativoText = document.getElementById('ativo-text');
        const scorePill = document.getElementById('score-pill');
        const precoPill = document.getElementById('preco-pill');
        const assertPill = document.getElementById('assert-pill');
        const hist = document.getElementById('historico');

        // Definir cores por tipo
        let bg = getComputedStyle(document.documentElement).getPropertyValue('--neutro-bg');
        let border = getComputedStyle(document.documentElement).getPropertyValue('--neutro-border');
        if (d.sinal.includes('COMPRA')) {
          bg = getComputedStyle(document.documentElement).getPropertyValue('--compra-bg');
          border = getComputedStyle(document.documentElement).getPropertyValue('--compra-border');
        } else if (d.sinal.includes('VENDA')) {
          bg = getComputedStyle(document.documentElement).getPropertyValue('--venda-bg');
          border = getComputedStyle(document.documentElement).getPropertyValue('--venda-border');
        }

        sigBox.style.background = bg;
        sigBox.style.borderColor = border;
        sinalText.innerText = d.sinal;
        ativoText.innerText = 'Ativo: ' + d.ativo;
        scorePill.innerText = 'Score: ' + (d.score ?? 0);
        precoPill.innerText = 'Preço: ' + (d.preco_entrada ? Number(d.preco_entrada).toFixed(6) : '0.000000');
        assertPill.innerText = 'Assert: ' + (d.assertPercentual ?? 'N/A');

        hist.innerHTML = d.historicoHtml || 'Sem histórico.';
        document.getElementById('refresh-seconds').innerText = d.refreshSeconds ?? 5;
      } catch(err){
        console.error('Erro ao processar SSE:', err);
      }
    };
  </script>
</body>
</html>
"""

# ====================== SSE ROUTE ======================
@app.route('/stream')
def stream():
    def event_stream():
        while True:
            try:
                with state_lock:
                    payload = {
                        'horario': ULTIMO_SINAL.get('horario'),
                        'sinal': ULTIMO_SINAL.get('sinal'),
                        'ativo': ULTIMO_SINAL.get('ativo'),
                        'score': ULTIMO_SINAL.get('score'),
                        'preco_entrada': ULTIMO_SINAL.get('preco_entrada'),
                        'assertPercentual': calcular_assertividade().get('percentual'),
                        'historicoHtml': formatar_historico_html(),
                        'refreshSeconds': DASHBOARD_REFRESH_RATE_SECONDS
                    }
                yield f"data: {json.dumps(payload)}\n\n"
                time.sleep(DASHBOARD_REFRESH_RATE_SECONDS)
            except GeneratorExit:
                break
            except Exception:
                traceback.print_exc()
                time.sleep(1)
    return Response(event_stream(), mimetype="text/event-stream")

# rota principal
@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

# ====================== INÍCIO DO APP ======================
if __name__ == '__main__':
    # porta padrão para Render/Heroku-style
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
