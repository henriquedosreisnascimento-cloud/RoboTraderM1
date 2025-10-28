# main.py
# =============================================================
# ROBÔ TRADER M1 - DASHBOARD AVANÇADO (PRONTO PARA DEPLOY)
# - Timeframe: M1 (1 minuto)
# - Dashboard com Chart.js + Histórico de trades + SSE
# - Sinais: COMPRA (verde), VENDA (vermelho), NEUTRO (amarelo)
# - Histórico simulado com SL/TP (0.05%) e checagem 1 minuto após sinal
# - Fallback de velas sintéticas caso KuCoin esteja indisponível
# - Pronto para Render (usa PORT env)
# =============================================================

from flask import Flask, Response, render_template_string, request
import requests
import time
import os
import copy
import traceback
import json
import random
from datetime import datetime, timedelta
import pytz
from threading import Thread, Lock

# ---------------- CONFIGURAÇÕES ----------------
TIMEZONE_BR = 'America/Sao_Paulo'
ATIVOS_MONITORADOS = ['BTC-USDT', 'ETH-USDT']  # ativos monitorados (ajuste se quiser)
ATIVO_PADRAO = 'BTC-USDT'
API_BASE_URL = 'https://api.kucoin.com/api/v1/market/candles'
INTERVALO_M1 = '1min'
NUM_VELAS_ANALISE = 60        # quantas velas manter no gráfico/análise (últimos N minutos)
SCORE_MINIMO_SINAL = 2       # score mínimo para sinal "forte"
MAX_HISTORICO = 100
PERCENTUAL_SL_TP = 0.0005    # 0.05% SL/TP
DASHBOARD_REFRESH_RATE_SECONDS = 4  # SSE refresh interval

# ---------------- FLASK & ESTADO ----------------
app = Flask(__name__)
state_lock = Lock()

def get_horario_brasilia():
    return datetime.now(pytz.timezone(TIMEZONE_BR))

# Estado global
ULTIMO_SINAL = {
    'horario': get_horario_brasilia().strftime('%H:%M:%S'),
    'ativo': ATIVO_PADRAO,
    'sinal': 'NEUTRO 🟡',
    'score': 0,
    'preco_entrada': 0.0
}

HISTORICO_SINAIS = []   # lista dos trades simulados (dicionários)
VELAS_CACHE = {a: [] for a in ATIVOS_MONITORADOS}
PENDING_CHECKS = []    # lista de sinais pendentes para checagem: dicts com 'check_time' e sinal data
ULTIMO_SINAL_CHECAR = None

# ---------------- UTILIDADES ----------------
def safe_get(url, params=None, timeout=8):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception:
        return None

def fallback_generate_velas(last_close=50000.0, n=NUM_VELAS_ANALISE):
    """Gera velas sintéticas (random walk) para fallback quando API cair."""
    velas = []
    price = float(last_close)
    for _ in range(n):
        change = random.uniform(-0.0015, 0.0015) * price
        open_p = price
        close_p = max(0.000001, price + change)
        high = max(open_p, close_p) * (1 + random.uniform(0.0, 0.0008))
        low = min(open_p, close_p) * (1 - random.uniform(0.0, 0.0008))
        velas.append([open_p, close_p, high, low])
        price = close_p
    return velas

def get_velas_kucoin(ativo, intervalo=INTERVALO_M1, limit=NUM_VELAS_ANALISE):
    """
    Retorna velas no formato cronológico: [ [o,c,h,l], ... ] com length <= limit.
    Usa fallback caso API falhe.
    """
    try:
        params = {'symbol': ativo, 'type': intervalo, 'limit': limit}
        r = safe_get(API_BASE_URL, params=params, timeout=8)
        if not r:
            # fallback
            cache = VELAS_CACHE.get(ativo, [])
            last_close = cache[-1][1] if cache else (50000.0 if ativo.startswith('BTC') else 1000.0)
            velas = fallback_generate_velas(last_close=last_close, n=limit)
            with state_lock:
                VELAS_CACHE[ativo] = velas
            return velas
        data = r.json().get('data', [])
        if not data:
            cache = VELAS_CACHE.get(ativo, [])
            last_close = cache[-1][1] if cache else (50000.0 if ativo.startswith('BTC') else 1000.0)
            velas = fallback_generate_velas(last_close=last_close, n=limit)
            with state_lock:
                VELAS_CACHE[ativo] = velas
            return velas
        parsed = []
        # KuCoin API: each v is [time, open, close, high, low, volume, turnover] depending on endpoint
        for v in data:
            try:
                o = float(v[1]); c = float(v[2]); h = float(v[3]); l = float(v[4])
                parsed.append([o, c, h, l])
            except Exception:
                continue
        parsed.reverse()  # order to chronological (oldest -> newest)
        parsed = parsed[-limit:]
        with state_lock:
            VELAS_CACHE[ativo] = parsed
        return parsed
    except Exception:
        traceback.print_exc()
        cache = VELAS_CACHE.get(ativo, [])
        last_close = cache[-1][1] if cache else (50000.0 if ativo.startswith('BTC') else 1000.0)
        velas = fallback_generate_velas(last_close=last_close, n=limit)
        with state_lock:
            VELAS_CACHE[ativo] = velas
        return velas

# ---------------- ESTRATÉGIA (Price Action simples) ----------------
def analisar_price_action(velas):
    """
    Score baseado nas 2 últimas velas:
    +1 por vela de alta, -1 por vela de baixa. Score range -2..+2.
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
        sinal = 'COMPRA 🟢'
    elif score < 0:
        sinal = 'VENDA 🔴'
    else:
        sinal = 'NEUTRO 🟡'

    return {'sinal': sinal, 'score': score, 'preco_entrada': float(c1)}

# ---------------- CHECAGEM DO RESULTADO (SL/TP) ----------------
def checar_resultado_sinal(sinal_checar):
    """
    Simula checagem do sinal na vela de expiração (próxima M1).
    Salva resultado no HISTORICO_SINAIS.
    """
    global HISTORICO_SINAIS
    try:
        ativo = sinal_checar.get('ativo', 'N/A')
        preco_entrada = float(sinal_checar.get('preco_entrada', 0.0) or 0.0)
        direcao = sinal_checar.get('sinal', 'NEUTRO 🟡')
        if ativo == 'N/A' or 'NEUTRO' in direcao:
            return

        velas = get_velas_kucoin(ativo, limit=2)
        if not velas:
            return

        o, c, h, l = velas[-1]
        resultado = 'NEUTRO'
        p = PERCENTUAL_SL_TP

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

        entry = {
            'horario': sinal_checar.get('horario', get_horario_brasilia().strftime('%H:%M:%S')),
            'ativo': ativo,
            'sinal': direcao,
            'resultado': resultado,
            'preco_entrada': preco_entrada,
            'preco_expiracao': float(c)
        }
        with state_lock:
            HISTORICO_SINAIS.append(entry)
            if len(HISTORICO_SINAIS) > MAX_HISTORICO:
                HISTORICO_SINAIS.pop(0)
    except Exception:
        traceback.print_exc()

def calcular_assertividade():
    with state_lock:
        if not HISTORICO_SINAIS:
            return {'total': 0, 'wins': 0, 'losses': 0, 'percentual': 'N/A'}
        wins = sum(1 for it in HISTORICO_SINAIS if 'WIN' in it['resultado'])
        total = len(HISTORICO_SINAIS)
        losses = total - wins
        percentual = f"{(wins/total)*100:.2f}%"
        return {'total': total, 'wins': wins, 'losses': losses, 'percentual': percentual}

# ---------------- PENDING CHECKS MONITOR ----------------
def pending_checks_worker():
    """Loop que verifica PENDING_CHECKS e executa checagem quando a hora chegar."""
    while True:
        try:
            now = datetime.utcnow()
            to_process = []
            with state_lock:
                remaining = []
                for item in PENDING_CHECKS:
                    # item: {'check_time': datetime_utc, 'sinal': {...}}
                    if item['check_time'] <= now:
                        to_process.append(item['sinal'])
                    else:
                        remaining.append(item)
                # update pending list
                PENDING_CHECKS[:] = remaining
            # process outside lock
            for sinal in to_process:
                try:
                    checar_resultado_sinal(sinal)
                except Exception:
                    traceback.print_exc()
        except Exception:
            traceback.print_exc()
        time.sleep(1)

# start pending checks worker thread
Thread(target=pending_checks_worker, daemon=True).start()

# ---------------- CICLO PRINCIPAL (GERAÇÃO DE SINAIS A CADA MINUTO) ----------------
def ciclo_analise():
    global ULTIMO_SINAL
    # Espera inicial curta para startup
    time.sleep(1)
    while True:
        try:
            # Alinha ao minuto: calcula segundos até o próximo minuto
            now_brasil = get_horario_brasilia()
            secs_to_next_min = 60 - now_brasil.second
            if secs_to_next_min <= 0:
                secs_to_next_min = 60
            # Dorme até início do próximo minuto
            time.sleep(secs_to_next_min)

            # Em início de minuto, coleta velas e gera sinais
            sinais = []
            for ativo in ATIVOS_MONITORADOS:
                velas = get_velas_kucoin(ativo, limit=NUM_VELAS_ANALISE)
                analise = analisar_price_action(velas)
                analise['ativo'] = ativo
                sinais.append(analise)

            # Seleciona o sinal com maior magnitude de score (abs)
            melhor = max(sinais, key=lambda x: abs(x.get('score', 0)))
            horario_str = get_horario_brasilia().strftime('%H:%M:%S')
            sinal_final = {
                'horario': horario_str,
                'ativo': melhor.get('ativo', ATIVO_PADRAO),
                'sinal': melhor.get('sinal', 'NEUTRO 🟡'),
                'score': melhor.get('score', 0),
                'preco_entrada': float(melhor.get('preco_entrada', 0.0) or 0.0)
            }

            with state_lock:
                ULTIMO_SINAL.update(sinal_final)
                # se sinal forte, adiciona ao PENDING_CHECKS para checar após 1 vela (1 minuto)
                if abs(sinal_final['score']) >= SCORE_MINIMO_SINAL and 'NEUTRO' not in sinal_final['sinal']:
                    # check_time em UTC = agora + 60s
                    check_time = datetime.utcnow() + timedelta(seconds=60)
                    PENDING_CHECKS.append({'check_time': check_time, 'sinal': copy.deepcopy(sinal_final)})

            print(f"[{horario_str}] Novo sinal: {sinal_final['ativo']} -> {sinal_final['sinal']} (score {sinal_final['score']})")

        except Exception:
            traceback.print_exc()
            # caso erro, aguarda 5 segundos e tenta de novo
            time.sleep(5)

# inicia thread de análise
Thread(target=ciclo_analise, daemon=True).start()

# ---------------- FRONT-END (HTML + Chart.js) ----------------
HTML_TEMPLATE = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Robô Trader M1 - Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  :root{
    --bg:#041022; --card:#0f1720; --muted:#9fb7cc; --accent:#6db8ff;
    --compra:#1fb954; --venda:#ff6161; --neutro:#ffd54a;
    --glass: rgba(255,255,255,0.03);
  }
  body{margin:0;font-family:Inter,system-ui,Arial;background:linear-gradient(180deg,#02101a 0%,#041427 100%);color:#eaf4ff}
  .wrap{max-width:1100px;margin:18px auto;padding:18px}
  header{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
  h1{margin:0;font-weight:700;color:var(--accent)}
  .grid{display:grid;grid-template-columns:380px 1fr;gap:16px}
  .card{background:var(--card);padding:14px;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,0.6)}
  .signal-box{display:flex;flex-direction:column;align-items:center;justify-content:center;height:220px;border-radius:10px;border:3px solid rgba(255,255,255,0.02);backdrop-filter: blur(6px)}
  .signal-text{font-size:1.2rem;font-weight:800;margin-bottom:8px}
  .muted{color:var(--muted);font-size:0.95rem}
  .pills{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
  .pill{background:var(--glass);padding:6px 10px;border-radius:999px;font-weight:700;font-size:0.95rem;border:1px solid rgba(255,255,255,0.02)}
  .chart-wrap{height:360px}
  .history-row{padding:8px 6px;border-bottom:1px solid rgba(255,255,255,0.03);display:flex;justify-content:space-between;align-items:center}
  .badge-win{background:rgba(31,185,84,0.12);color:var(--compra);padding:6px 10px;border-radius:8px;font-weight:800}
  .badge-loss{background:rgba(255,97,97,0.08);color:var(--venda);padding:6px 10px;border-radius:8px;font-weight:800}
  .badge-neutral{background:rgba(255,213,74,0.08);color:var(--neutro);padding:6px 10px;border-radius:8px;font-weight:800}
  footer{margin-top:14px;color:var(--muted);text-align:center;font-size:0.9rem}
  @media (max-width:900px){ .grid{grid-template-columns:1fr} .chart-wrap{height:260px} }
  .table-header{display:flex;justify-content:space-between;font-weight:700;color:var(--muted);padding:6px 0}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>🚀 Robô Trader M1</h1>
      <div class="muted" id="server-time">--:--:--</div>
    </div>
    <div style="text-align:right">
      <div class="muted">Timeframe: M1 • Atualiza a cada <strong id="refresh">{{refresh}}</strong>s</div>
      <div style="margin-top:8px;">
        <select id="select-asset" style="padding:8px;border-radius:8px;background:#071323;border:1px solid rgba(255,255,255,0.03);color:#eaf4ff">
          {% for a in ativos %}
            <option value="{{a}}" {% if a==ativo %}selected{% endif %}>{{a}}</option>
          {% endfor %}
        </select>
        <button id="btn-refresh" style="padding:8px 12px;border-radius:8px;border:none;background:var(--accent);color:#041022;font-weight:800;cursor:pointer;margin-left:8px">Forçar</button>
      </div>
    </div>
  </header>

  <div class="grid">
    <div class="card">
      <div class="signal-box" id="signal-box">
        <div class="signal-text" id="signal-text">Aguardando sinal...</div>
        <div class="muted" id="asset-text">Ativo: {{ativo}}</div>
        <div class="pills">
          <div class="pill" id="score-pill">Score: 0</div>
          <div class="pill" id="price-pill">Preço: 0.000000</div>
          <div class="pill" id="assert-pill">Assert: N/A</div>
        </div>
      </div>

      <div style="margin-top:12px">
        <div style="font-weight:800;margin-bottom:6px">Assertividade</div>
        <div style="background:rgba(255,255,255,0.03);border-radius:8px;padding:10px;display:flex;align-items:center;gap:12px">
          <div style="flex:1">
            <div style="height:10px;background:rgba(255,255,255,0.04);border-radius:999px;overflow:hidden">
              <div id="assert-bar" style="height:10px;width:0%;background:linear-gradient(90deg,#57e389,#16a34a)"></div>
            </div>
          </div>
          <div id="assert-text" style="min-width:70px;text-align:right;font-weight:800">N/A</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <div style="font-weight:800">Gráfico (Últimos {{num_velas}} minutos)</div>
        <div class="muted">Intervalo: 1min</div>
      </div>
      <div class="chart-wrap">
        <canvas id="priceChart"></canvas>
      </div>
    </div>
  </div>

  <div class="card" style="margin-top:14px">
    <div class="table-header">
      <div style="font-weight:800">Histórico de Trades (simulado)</div>
      <div class="muted">Últimos {{max_historico}} operações</div>
    </div>
    <div id="history-rows" style="margin-top:8px"></div>
  </div>

  <footer>Simulação: SL/TP = {{sltp_percent}}%. Em produção ajuste risco e integre com API segura da exchange.</footer>
</div>

<script>
const refreshSeconds = {{refresh}};
document.getElementById('refresh').innerText = refreshSeconds;
const ctx = document.getElementById('priceChart').getContext('2d');
let priceChart = null;
function createChart(labels, closes){
  const data = { labels: labels, datasets: [{ label: 'Fechamento', data: closes, borderColor: '#6db8ff', backgroundColor: 'rgba(109,184,255,0.06)', tension: 0.25, fill: true, pointRadius: 0 }]};
  const cfg = { type: 'line', data: data, options: { maintainAspectRatio: false, scales: { x: { display: false }, y: { ticks: { color: '#9fb4d6' } } }, plugins: { legend: { display: false } } } };
  if(priceChart) priceChart.destroy();
  priceChart = new Chart(ctx, cfg);
}

function renderHistoryRows(list){
  const container = document.getElementById('history-rows');
  if(!list || list.length===0){ container.innerHTML = '<div class="muted">Sem operações registradas.</div>'; return; }
  let html = '';
  list.forEach(it=>{
    const badge = it.resultado.includes('WIN') ? `<span class="badge-win">${it.resultado}</span>` : it.resultado.includes('LOSS') ? `<span class="badge-loss">${it.resultado}</span>` : `<span class="badge-neutral">${it.resultado}</span>`;
    const diff = (it.preco_expiracao - it.preco_entrada).toFixed(6);
    html += `<div class="history-row"><div><strong>${it.ativo}</strong><div class="muted">${it.horario}</div></div><div>${it.sinal}<br>${badge}</div><div style="text-align:right">Δ ${diff}</div></div>`;
  });
  container.innerHTML = html;
}

const evt = new EventSource('/stream');
evt.onmessage = function(e){
  try {
    const d = JSON.parse(e.data);
    document.getElementById('server-time').innerText = d.horario;
    const box = document.getElementById('signal-box');
    const signalText = document.getElementById('signal-text');
    const assetText = document.getElementById('asset-text');
    const scorePill = document.getElementById('score-pill');
    const pricePill = document.getElementById('price-pill');
    const assertText = document.getElementById('assert-text');
    const assertBar = document.getElementById('assert-bar');

    signalText.innerText = d.sinal;
    assetText.innerText = 'Ativo: ' + d.ativo;
    scorePill.innerText = 'Score: ' + (d.score ?? 0);
    pricePill.innerText = 'Preço: ' + (d.preco_entrada ? Number(d.preco_entrada).toFixed(6) : '0.000000');
    assertText.innerText = d.assertPercentual ?? 'N/A';
    const percent = (d.assertPercentual && d.assertPercentual !== 'N/A') ? parseFloat(d.assertPercentual.replace('%','')) : 0;
    assertBar.style.width = (percent>100?100:percent)+'%';

    if(d.sinal.includes('COMPRA')){ box.style.borderColor = 'var(--compra)'; box.style.boxShadow = '0 6px 30px rgba(31,185,84,0.08)'; }
    else if(d.sinal.includes('VENDA')){ box.style.borderColor = 'var(--venda)'; box.style.boxShadow = '0 6px 30px rgba(255,97,97,0.06)'; }
    else { box.style.borderColor = 'var(--neutro)'; box.style.boxShadow = 'none'; }

    const labels = d.chart.labels || [];
    const closes = d.chart.closes || [];
    createChart(labels, closes);

    renderHistoryRows(d.historicoList || []);
  } catch(err){
    console.error('SSE parse error', err);
  }
};

document.getElementById('select-asset').addEventListener('change', function(){
  fetch('/set_asset', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({asset:this.value})});
});
document.getElementById('btn-refresh').addEventListener('cli
