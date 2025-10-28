# main.py
# ROBÔ TRADER M1 - DASHBOARD COLORIDO + SL/TP SIMULADO

from flask import Flask, Response, render_template_string
import requests, time, os, copy, traceback
from datetime import datetime
import pytz
from threading import Thread, Lock

# ====================== CONFIGURAÇÕES ======================
TIMEZONE_BR = 'America/Sao_Paulo'
ATIVOS_MONITORADOS = ['BTC-USDT', 'ETH-USDT', 'EUR-USDT']
API_BASE_URL = 'https://api.kucoin.com/api/v1/market/candles'
INTERVALO_M1 = '1min'
NUM_VELAS_ANALISE = 3
SCORE_MINIMO_SINAL = 2
MAX_HISTORICO = 10
PERCENTUAL_SL_TP = 0.0005
DASHBOARD_REFRESH_RATE_SECONDS = 5

# ====================== FLASK ======================
app = Flask(__name__)
state_lock = Lock()

# ====================== VARIÁVEIS ======================
def get_horario_brasilia():
    return datetime.now(pytz.timezone(TIMEZONE_BR))

ULTIMO_SINAL = {'horario': get_horario_brasilia().strftime('%H:%M:%S'),
                'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'score': 0, 'preco_entrada': 0.0}

HISTORICO_SINAIS = []
ULTIMO_SINAL_CHECAR = None

# ====================== FUNÇÕES ======================
def get_velas_kucoin(ativo):
    try:
        params = {'symbol': ativo, 'type': INTERVALO_M1}
        r = requests.get(API_BASE_URL, params=params, timeout=8)
        r.raise_for_status()
        data = r.json().get('data', [])
        velas = [[float(v[1]), float(v[2]), float(v[3]), float(v[4])] for v in data]
        return velas[-(NUM_VELAS_ANALISE+1):]
    except:
        return []

def analisar_price_action(velas):
    if len(velas) < 2: return {'sinal':'NEUTRO 🟡','score':0,'preco_entrada':0.0}
    o1,c1 = velas[-1][0], velas[-1][1]
    o2,c2 = velas[-2][0], velas[-2][1]
    score=0
    score += 1 if c1>o1 else -1 if c1<o1 else 0
    score += 1 if c2>o2 else -1 if c2<o2 else 0
    if score>=SCORE_MINIMO_SINAL: sinal='COMPRA FORTE 🚀'
    elif score<=-SCORE_MINIMO_SINAL: sinal='VENDA FORTE 📉'
    elif score>0: sinal='COMPRA Fraca 🟢'
    elif score<0: sinal='VENDA Fraca 🔴'
    else: sinal='NEUTRO 🟡'
    return {'sinal':sinal,'score':score,'preco_entrada':c1}

def checar_resultado_sinal(sinal_checar):
    global HISTORICO_SINAIS
    try:
        ativo = sinal_checar['ativo']
        preco_entrada = sinal_checar['preco_entrada']
        direcao = sinal_checar['sinal']
        if ativo=='N/A' or 'NEUTRO' in direcao: return
        velas = get_velas_kucoin(ativo)
        if not velas: return
        o,c,h,l = velas[-1]
        resultado='NEUTRO'
        p=PERCENTUAL_SL_TP
        if 'COMPRA' in direcao:
            tp=preco_entrada*(1+p); sl=preco_entrada*(1-p)
            if h>=tp: resultado='WIN ✅ (TP)'
            elif l<=sl: resultado='LOSS ❌ (SL)'
            else: resultado='WIN ✅ (Close)' if c>preco_entrada else 'LOSS ❌ (Close)'
        elif 'VENDA' in direcao:
            tp=preco_entrada*(1-p); sl=preco_entrada*(1+p)
            if l<=tp: resultado='WIN ✅ (TP)'
            elif h>=sl: resultado='LOSS ❌ (SL)'
            else: resultado='WIN ✅ (Close)' if c<preco_entrada else 'LOSS ❌ (Close)'
        with state_lock:
            HISTORICO_SINAIS.append({'horario':sinal_checar['horario'],'ativo':ativo,'sinal':direcao,'resultado':resultado,'preco_entrada':preco_entrada,'preco_expiracao':c})
            if len(HISTORICO_SINAIS)>MAX_HISTORICO: HISTORICO_SINAIS.pop(0)
    except:
        traceback.print_exc()

def formatar_historico_html(historico):
    linhas=[]
    for i in reversed(historico):
        classe='win' if 'WIN' in i['resultado'] else 'loss'
        diff=i['preco_expiracao']-i['preco_entrada']
        linhas.append(f"[{i['horario']}] {i['ativo']} -> <span class='{classe}'>{i['resultado']}</span> ({i['sinal']}, Diff: {diff:.5f})")
    return '<br>'.join(linhas)

# ====================== CICLO DE ANÁLISE ======================
def ciclo_analise():
    global ULTIMO_SINAL, ULTIMO_SINAL_CHECAR
    time.sleep(1)
    while True:
        try:
            now=get_horario_brasilia()
            sleep_time=60-now.second
            if ULTIMO_SINAL_CHECAR: checar_resultado_sinal(ULTIMO_SINAL_CHECAR); ULTIMO_SINAL_CHECAR=None
            sinais=[{'ativo':a, **analisar_price_action(get_velas_kucoin(a))} for a in ATIVOS_MONITORADOS]
            melhor=max(sinais,key=lambda x: abs(x['score']))
            sinal_final={'horario':now.strftime('%H:%M:%S'),'ativo':melhor['ativo'],'sinal':melhor['sinal'],'score':melhor['score'],'preco_entrada':melhor['preco_entrada']}
            with state_lock:
                if abs(sinal_final['score'])>=SCORE_MINIMO_SINAL: ULTIMO_SINAL_CHECAR=copy.deepcopy(sinal_final)
                ULTIMO_SINAL.update(sinal_final)
            print(f"[{sinal_final['horario']}] 📢 {sinal_final['ativo']} - {sinal_final['sinal']} (Score:{sinal_final['score']})")
        except:
            traceback.print_exc()
        time.sleep(sleep_time)

Thread(target=ciclo_analise,daemon=True).start()

# ====================== DASHBOARD ======================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Dashboard Trader M1</title>
<style>
body{background:#121212;color:#fff;font-family:sans-serif;margin:0;padding:0;}
.header{padding:10px;text-align:center;font-size:1.5em;font-weight:bold;}
.signal-box{margin:20px;padding:15px;border-radius:10px;}
.win{color:#0f0;}
.loss{color:#f00;}
.neutro{color:#ff0;}
.last-signal-box{border-left:5px solid;}
.data-item{margin:5px 0;}
.signal-active{animation:pulse 1s infinite;box-shadow:0 0 20px #fff;}
@keyframes pulse{0%{transform:scale(1);}50%{transform:scale(1.05);}100%{transform:scale(1);}}
</style>
</head>
<body>
<div class="header">ROBÔ TRADER M1 - DASHBOARD</div>
<div id="signal" class="signal-box"></div>
<div id="last-signal" class="last-signal-box"></div>
<div id="historico"></div>

<script>
const evtSource=new EventSource("/stream");
evtSource.onmessage=function(e){
    const data=JSON.parse(e.data);
    const sBox=document.getElementById("signal");
    const lBox=document.getElementById("last-signal");
    const hBox=document.getElementById("historico");
    let bg="#ff0", border="#ff0", text="NEUTRO";
    if(data.sinal.includes("COMPRA")){bg="#0f0"; border="#0a0"; text="COMPRA"}
    else if(data.sinal.includes("VENDA")){bg="#f00"; border="#a00"; text="VENDA"}
    sBox.style.backgroundColor=bg;
    sBox.style.border="2px solid "+border;
    sBox.innerHTML=`<strong>${text}</strong><br>Ativo: ${data.ativo}<br>Score: ${data.score}<br>Preço: ${data.preco_entrada.toFixed(5)}`;
    lBox.style.borderLeft="5px solid "+border;
    lBox.innerHTML=`Último Sinal: ${data.sinal}<br>Hora: ${data.horario}`;
    hBox.innerHTML="<h3>Histórico Simulado</h3>"+data.historicoHtml;
};
</script>
</body>
</html>
"""

@app.route("/")
def index(): return render_template_string(HTML_TEMPLATE)

@app.route("/stream")
def stream():
    def event_stream():
        while True:
            with state_lock:
                payload={'sinal':ULTIMO_SINAL['sinal'],'ativo':ULTIMO_SINAL['ativo'],'score':ULTIMO_SINAL['score'],'preco_entrada':ULTIMO_SINAL['preco_entrada'],'horario':ULTIMO_SINAL['horario'],'historicoHtml':formatar_historico_html(HISTORICO_SINAIS)}
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(DASHBOARD_REFRESH_RATE_SECONDS)
    return Response(event_stream(), mimetype="text/event-stream")

# ====================== RUN ======================
if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
