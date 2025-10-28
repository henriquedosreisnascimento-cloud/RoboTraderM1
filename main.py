# main.py
# =============================================================
# ROBÔ TRADER M1 - DASHBOARD AVANÇADO (GRÁFICO + HISTÓRICO REAL-TIME)
# - Chart.js no front-end para gráfico
# - Histórico com WIN/LOSS marcado automaticamente
# - Fallback gerador de velas se API KuCoin falhar (para testes)
# - Pronto para Render/GitHub (usa PORT env)
# =============================================================

from flask import Flask, Response, render_template_string, request
import requests, time, os, copy, traceback, json, random
from datetime import datetime
import pytz
from threading import Thread, Lock

# ---------------- CONFIGURAÇÕES ----------------
TIMEZONE_BR = 'America/Sao_Paulo'
ATIVOS_MONITORADOS = ['BTC-USDT', 'ETH-USDT', 'EUR-USDT']  # ativos monitorados
ATIVO_PADRAO = 'BTC-USDT'
API_BASE_URL = 'https://api.kucoin.com/api/v1/market/candles'
INTERVALO_M1 = '1min'
NUM_VELAS_ANALISE = 30        # quantas velas manter no gráfico/análise
SCORE_MINIMO_SINAL = 2       # score mínimo para sinal "forte"
MAX_HISTORICO = 50
PERCENTUAL_SL_TP = 0.0005    # 0.05% SL/TP
DASHBOARD_REFRESH_RATE_SECONDS = 4

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
HISTORICO_SINAIS = []  # lista de dicts com resultado
VELAS_CACHE = {a: [] for a in ATIVOS_MONITORADOS}  # cache local de velas por ativo
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
    """Gera velas sintéticas simples (random walk) para fallback quando API cair."""
    velas = []
    price = float(last_close)
    for _ in range(n):
        # variação pequena
        change = random.uniform(-0.002, 0.002) * price
        open_p = price
        close_p = max(0.000001, price + change)
        high = max(open_p, close_p) * (1 + random.uniform(0.0, 0.001))
        low = min(open_p, close_p) * (1 - random.uniform(0.0, 0.001))
        velas.append([open_p, close_p, high, low])
        price = close_p
    return velas

def get_velas_kucoin(ativo, intervalo=INTERVALO_M1, limit=NUM_VELAS_ANALISE):
    """
    Retorna velas no formato cronológico: [ [o,c,h,l], ... ] com length <= limit.
    Se a API falhar, usa fallback synthetic candles (e garante que VELAS_CACHE tenha dados).
    """
    try:
        params = {'symbol': ativo, 'type': intervalo, 'limit': limit}
        r = safe_get(API_BASE_URL, params=params, timeout=8)
        if not r:
            # fallback
            cache = VELAS_CACHE.get(ativo)
            last_close = cache[-1][1] if cache else (50000.0 if ativo.startswith('BTC') else 1000.0)
            velas = fallback_generate_velas(last_close=last_close, n=limit)
            # update cache
            with state_lock:
                VELAS_CACHE[ativo] = velas
            return velas
        data = r.json().get('data', [])
        if not data:
            # fallback again
            cache = VELAS_CACHE.get(ativo)
            last_close = cache[-1][1] if cache else (50000.0 if ativo.startswith('BTC') else 1000.0)
            velas = fallback_generate_velas(last_close=last_close, n=limit)
            with state_lock:
                VELAS_CACHE[ativo] = velas
            return velas
        # Data geralmente vêm em ordem decrescente (mais recente primeiro) — convertemos para cronológico
        parsed = []
        for v in data:
            try:
                o = float(v[1]); c = float(v[2]); h = float(v[3]); l = float(v[4])
                parsed.append([o, c, h, l])
            except Exception:
                continue
        parsed.reverse()
        parsed = parsed[-limit:]
        with state_lock:
            VELAS_CACHE[ativo] = parsed
        return parsed
    except Exception:
        traceback.print_exc()
        # ultimate fallback
        cache = VELAS_CACHE.get(ativo)
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
    Salva resultado em HISTORICO_SINAIS com WIN/LOSS/TP/SL.
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

# ---------------- CICLO PRINCIPAL (BACKGROUND) ----------------
def ciclo_analise():
    global ULTIMO_SINAL, ULTIMO_SINAL_CHECAR
    print("🔎 Iniciando ciclo de análise (background)...")
    while True:
        try:
            sinais = []
            # coleta e analisa cada ativo
            for ativo in ATIVOS_MONITORADOS:
                velas = get_velas_kucoin(ativo, limit=NUM_VELAS_ANALISE)
                analise = analisar_price_action(velas)
                analise['ativo'] = ativo
                sinais.append(analise)

            # escolhe o sinal com maior magnitude de score
            melhor = max(sinais, key=lambda x: abs(x.get('score', 0)))
            now = get_horario_brasilia().strftime('%H:%M:%S')
            sinal_final = {
                'horario': now,
                'ativo': melhor.get('ativo', ATIVO_PADRAO),
                'sinal': melhor.get('sinal', 'NEUTRO 🟡'),
                'score': melhor.get('score', 0),
                'preco_entrada': float(melhor.get('preco_entrada', 0.0) or 0.0)
            }

            with state_lock:
                ULTIMO_SINAL.update(sinal_final)
                # se for forte, marcar para checagem (será checado imediatamente após atualização — simulação rápida)
                if abs(sinal_final['score']) >= SCORE_MINIMO_SINAL and 'NEUTRO' not in sinal_final['sinal']:
                    ULTIMO_SINAL_CHECAR = copy.deepcopy(sinal_final)

            print(f"[{sinal_final['horario']}] Sinal selecionado: {sinal_final['ativo']} -> {sinal_final['sinal']} (score {sinal_final['score']})")

            # Checagem: vamos simular que a próxima vela (após 1 ciclo) define resultado.
            # Para tornar "real-time" no demo, espera pequena e checa; em produção esperar 60s/1 vela.
            if ULTIMO_SINAL_CHECAR:
                # aguarda um pouco para "esperar" vela de expiração (aqui usamos 1s para demo; em produção ajustar para 60)
                time.sleep(1)
                checar_resultado_sinal(ULTIMO_SINAL_CHECAR)
                with state_lock:
                    ULTIMO_SINAL_CHECAR = None

        except Exception:
            traceback.print_exc()

        # Espera antes do próximo ciclo — em produção poderia ser alinhado ao minuto
        time.sleep(5)

# inicia thread
Thread(target=ciclo_analise, daemon=True).start()

# ---------------- FRONT-END (HTML + Chart.js) ----------------
HTML_TEMPLATE = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Robô Trader M1 - Dashboard</title>

<!-- Chart.js CDN -->
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>
  :root{
    --bg:#07101a; --card:#0f1a26; --muted:#93a4b8;
    --accent:#6db8ff; --text:#e7f0fb;
    --compra:#1aae3a; --venda:#ff5b5b; --neutro:#ffcf33;
  }
  body{margin:0;font-family:Inter,system-ui,Arial;background:linear-gradient(180deg,#04111a 0%,#071427 100%);color:var(--text)}
  .wrap{max-width:1100px;margin:18px auto;padding:18px}
  header{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
  header h1{margin:0;font-weight:600;color:var(--accent)}
  .grid{display:grid;grid-template-columns:380px 1fr;gap:16px}
  .card{background:var(--card);padding:14px;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,0.6)}
  .signal-box{display:flex;flex-direction:column;align-items:center;justify-content:center;height:220px;border-radius:10px;border:3px solid rgba(255,255,255,0.02)}
  .signal-text{font-size:1.2rem;font-weight:700;margin-bottom:8px}
  .muted{color:var(--muted);font-size:0.95rem}
  .pills{display:flex;gap:8px;margin-top:8px}
  .pill{background:rgba(255,255,255,0.03);padding:6px 10px;border-radius:999px;font-weight:600;font-size:0.95rem}
  .chart-wrap{height:360px}
  .history-table{width:100%;border-collapse:collapse;margin-top:10px}
  .history-row{padding:8px 6px;border-bottom:1px solid rgba(255,255,255,0.03)}
  .badge-win{background:rgba(25,200,100,0.12);color:var(--compra);padding:4px 8px;border-radius:8px;font-weight:700}
  .badge-loss{background:rgba(255,90,90,0.08);color:var(--venda);padding:4px 8px;border-radius:8px;font-weight:700}
  .badge-neutral{background:rgba(255,205,30,0.08);color:var(--neutro);padding:4px 8px;border-radius:8px;font-weight:700}
  footer{margin-top:14px;color:var(--muted);text-align:center;font-size:0.9rem}
  @media (max-width:900px){
    .grid{grid-template-columns:1fr; }
    .chart-wrap{height:260px}
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>🚀 Robô Trader M1</h1>
    <div>
      <div class="muted">Dashboard • Atualiza a cada <strong id="refresh">4</strong>s</div>
      <div class="muted" id="server-time"></div>
    </div>
  </header>

  <div class="grid">
    <!-- Left panel: signal + info -->
    <div class="card">
      <div class="signal-box" id="signal-box" style="background:linear-gradient(180deg, rgba(255,255,255,0.01), transparent)">
        <div class="signal-text" id="signal-text">Aguardando sinal...</div>
        <div class="muted" id="asset-text">Ativo: {{ativo}}</div>
        <div class="pills">
          <div class="pill" id="score-pill">Score: 0</div>
          <div class="pill" id="price-pill">Preço: 0.000000</div>
          <div class="pill" id="assert-pill">Assert: N/A</div>
        </div>
      </div>

      <div style="margin-top:12px; display:flex;gap:8px;flex-wrap:wrap">
        <select id="select-asset" style="flex:1;padding:8px;border-radius:8px;background:#081522;border:1px solid rgba(255,255,255,0.03);color:var(--text)">
          {% for a in ativos %}
            <option value="{{a}}" {% if a==ativo %}selected{% endif %}>{{a}}</option>
          {% endfor %}
        </select>
        <button id="btn-refresh" style="padding:8px 12px;border-radius:8px;border:none;background:var(--accent);color:#011827;font-weight:700;cursor:pointer">Forçar</button>
      </div>

      <div style="margin-top:12px">
        <div style="font-weight:700;margin-bottom:6px">Assertividade</div>
        <div style="background:rgba(255,255,255,0.03);border-radius:8px;padding:10px;display:flex;align-items:center;gap:12px">
          <div style="flex:1">
            <div style="height:10px;background:rgba(255,255,255,0.04);border-radius:999px;overflow:hidden">
              <div id="assert-bar" style="height:10px;width:0%;background:linear-gradient(90deg,#57e389,#16a34a)"></div>
            </div>
          </div>
          <div id="assert-text" style="min-width:70px;text-align:right;font-weight:700">N/A</div>
        </div>
      </div>
    </div>

    <!-- Right panel: chart -->
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <div style="font-weight:700">Gráfico (Últimas velas)</div>
        <div class="muted">Intervalo: 1min</div>
      </div>
      <div class="chart-wrap">
        <canvas id="priceChart"></canvas>
      </div>
    </div>
  </div>

  <!-- Histórico -->
  <div class="card" style="margin-top:14px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <div style="font-weight:700">Histórico de Trades (simulado)</div>
      <div class="muted">Últimos {MAX_HISTORICO} operações</div>
    </div>
    <div id="history-rows">
      <!-- populated by JS -->
    </div>
  </div>

  <footer>Este painel é uma simulação. Verifique sempre antes de operar com capital real.</footer>
</div>

<script>
const refreshSeconds = {{refresh}};
document.getElementById('refresh').innerText = refreshSeconds;
const ctx = document.getElementById('priceChart').getContext('2d');
let priceChart = null;
function createChart(labels, closes){
  const data = {
    labels: labels,
    datasets: [{
      label: 'Fechamento',
      data: closes,
      borderColor: '#6db8ff',
      backgroundColor: 'rgba(109,184,255,0.06)',
      tension: 0.2,
      fill: true,
      pointRadius: 0
    }]
  };
  const cfg = {
    type: 'line',
    data: data,
    options: {
      maintainAspectRatio: false,
      scales: {
        x: { display: false },
        y: { ticks: { color: '#9fb4d6' } }
      },
      plugins: { legend: { display: false } }
    }
  };
  if(priceChart) priceChart.destroy();
  priceChart = new Chart(ctx, cfg);
}

function renderHistoryRows(list){
  const container = document.getElementById('history-rows');
  if(!list || list.length===0){ container.innerHTML = '<div class="muted">Sem operações registradas.</div>'; return; }
  let html = '<table style="width:100%">';
  list.forEach(it=>{
    const badge = it.resultado.includes('WIN') ? `<span class="badge-win">${it.resultado}</span>` : it.resultado.includes('LOSS') ? `<span class="badge-loss">${it.resultado}</span>` : `<span class="badge-neutral">${it.resultado}</span>`;
    const diff = (it.preco_expiracao - it.preco_entrada).toFixed(6);
    html += `<tr class="history-row"><td style="width:220px"><strong>${it.ativo}</strong><br><span class="muted">${it.horario}</span></td><td>${it.sinal}<br>${badge}</td><td style="text-align:right">Δ ${diff}</td></tr>`;
  });
  html += '</table>';
  container.innerHTML = html;
}

// SSE
const evt = new EventSource('/stream');
evt.onmessage = function(e){
  try{
    const d = JSON.parse(e.data);
    // update top boxes
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

    // color signal box
    if(d.sinal.includes('COMPRA')){ box.style.borderColor = 'var(--compra)'; box.style.boxShadow = '0 6px 30px rgba(26, 190, 118,0.08)'; }
    else if(d.sinal.includes('VENDA')){ box.style.borderColor = 'var(--venda)'; box.style.boxShadow = '0 6px 30px rgba(255, 90, 90,0.06)'; }
    else { box.style.borderColor = 'var(--neutro)'; box.style.boxShadow = 'none'; }

    // chart update (labels & closes)
    const labels = d.chart.labels || [];
    const closes = d.chart.closes || [];
    createChart(labels, closes);

    // history
    renderHistoryRows(d.historicoList || []);
    document.getElementById('server-time').innerText = d.horario;
  } catch(ex){ console.error('SSE parse error', ex); }
};

// select asset handling
document.getElementById('select-asset').addEventListener('change', function(){
  fetch('/set_asset', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({asset:this.value})});
});

// manual refresh
document.getElementById('btn-refresh').addEventListener('click', function(){
  fetch('/force_refresh', {method:'POST'});
});
</script>
</body>
</html>
"""

# ---------------- ROTAS AUX ----------------
@app.route('/set_asset', methods=['POST'])
def set_asset():
    data = request.get_json() or {}
    asset = data.get('asset')
    if asset and asset in ATIVOS_MONITORADOS:
        with state_lock:
            ULTIMO_SINAL['ativo'] = asset
    return ('', 204)

@app.route('/force_refresh', methods=['POST'])
def force_refresh():
    # apenas força a atualização (não necessário fazer nada especial aqui)
    return ('', 204)

# ---------------- SSE (dados enviados ao front) ----------------
@app.route('/stream')
def stream():
    def event_stream():
        while True:
            try:
                with state_lock:
                    # monta chart simples (últimos N fechamentos) do ativo atual
                    ativo = 
