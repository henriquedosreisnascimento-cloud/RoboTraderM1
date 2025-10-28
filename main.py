# main.py
# ROBÔ TRADER M1 (FLUIDO) - DASHBOARD COLORIDO
# Estratégia RSI / BB / Momentum + Interface Tecnológica

from flask import Flask, Response, render_template_string
import requests
import time
from datetime import datetime
import pytz
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor
import copy
import traceback
import json
import os  # Corrigido para o Render

# ====================== CONFIGURAÇÕES ======================
TIMEZONE_BR = 'America/Sao_Paulo'
ATIVOS_MONITORADOS = ['BTC-USDT', 'ETH-USDT', 'EUR-USDT', 'DOT-USDT', 'ADA-USDT'] 
API_BASE_URL = 'https://api.kucoin.com/api/v1/market/candles'
INTERVALO_M1 = '1min'
NUM_VELAS_ANALISE_M1 = 30 
MAX_WORKERS = 5
ASSERTIVIDADE_MINIMA = 80.0
MAX_HISTORICO = 10
PERCENTUAL_SL_TP = 0.0005
PERIOD_BB = 14
STD_DEV_BB = 2
PERIOD_RSI = 14
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
DASHBOARD_REFRESH_RATE_SECONDS = 5
URL_ALERTE_SONORO = "https://www.soundhelix.com/examples/audio/Wave-beep.wav"

# ====================== FLASK ======================
app = Flask(__name__)
state_lock = Lock()

# ====================== VARIÁVEIS ======================
def get_horario_brasilia():
    return datetime.now(pytz.timezone(TIMEZONE_BR))

ULTIMO_SINAL = {'horario': get_horario_brasilia().strftime('%H:%M:%S'), 'ativo': 'N/A', 'sinal': 'NEUTRO 🟡', 'assertividade': 0.0, 'preco_entrada': 0.0}
ULTIMO_SINAL_REGISTRADO = {'horario': 'N/A', 'sinal_tipo': 'N/A'}
HISTORICO_SINAIS = []
ULTIMO_SINAL_CHECAR = None

# ====================== INDICADORES ======================
def calculate_rsi(velas, period=PERIOD_RSI):
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
    return 100 - (100 / (1 + rs))

def calculate_bollinger_bands(velas, period=PERIOD_BB, std_dev=STD_DEV_BB):
    if len(velas) < period:
        last_close = velas[-1][1] if velas else 0.0
        return {'upper': last_close*1.001, 'mid': last_close, 'lower': last_close*0.999}
    closes = [v[1] for v in velas[-period:]]
    sma = sum(closes)/period
    variance = sum([(c-sma)**2 for c in closes])/period
    std_dev_val = variance**0.5
    return {'upper': sma + std_dev_val*std_dev, 'mid': sma, 'lower': sma - std_dev_val*std_dev}

# ====================== FUNÇÕES DE DADOS ======================
def get_velas_kucoin(ativo, intervalo, limit=NUM_VELAS_ANALISE_M1):
    try:
        params = {'symbol': ativo, 'type': intervalo, 'limit': limit}
        r = requests.get(API_BASE_URL, params=params, timeout=8)
        r.raise_for_status()
        data = r.json().get('data', [])
        velas = [[float(v[1]), float(v[2]), float(v[3]), float(v[4])] for v in data]
        return velas
    except Exception as e:
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] ⚠️ Erro {ativo}: {e}")
        return []

def checar_resultado_sinal(sinal_checar):
    global HISTORICO_SINAIS
    try:
        ativo = sinal_checar['ativo']
        preco_entrada = sinal_checar['preco_entrada']
        direcao_sinal = sinal_checar['sinal']
        if ativo=='N/A' or 'NEUTRO' in direcao_sinal or sinal_checar['assertividade']<ASSERTIVIDADE_MINIMA: return
        velas_exp = get_velas_kucoin(ativo, INTERVALO_M1, limit=1)
        if len(velas_exp)<1: return
        o_exp, c_exp, h_exp, l_exp = velas_exp[-1]
        resultado='NEUTRO'
        p=PERCENTUAL_SL_TP
        if 'COMPRA' in direcao_sinal:
            tp = preco_entrada*(1+p)
            sl = preco_entrada*(1-p)
            if h_exp>=tp: resultado='WIN ✅ (TP)'
            elif l_exp<=sl: resultado='LOSS ❌ (SL)'
            else: resultado='WIN ✅ (Close)' if c_exp>preco_entrada else 'LOSS ❌ (Close)'
        elif 'VENDA' in direcao_sinal:
            tp = preco_entrada*(1-p)
            sl = preco_entrada*(1+p)
            if l_exp<=tp: resultado='WIN ✅ (TP)'
            elif h_exp>=sl: resultado='LOSS ❌ (SL)'
            else: resultado='WIN ✅ (Close)' if c_exp<preco_entrada else 'LOSS ❌ (Close)'
        with state_lock:
            HISTORICO_SINAIS.append({'horario':sinal_checar['horario'],'ativo':ativo,'sinal':direcao_sinal,'assertividade':sinal_checar['assertividade'],'resultado':resultado,'preco_entrada':preco_entrada,'preco_expiracao':c_exp})
            if len(HISTORICO_SINAIS)>MAX_HISTORICO: HISTORICO_SINAIS.pop(0)
        print(f"[{get_horario_brasilia().strftime('%H:%M:%S')}] 🎯 Resultado {ativo}: {resultado}")
    except Exception:
        print("Erro em checar_resultado_sinal:")
        traceback.print_exc()

def formatar_historico_html(historico):
    linhas=[]
    for item in reversed(historico):
        classe='win' if 'WIN' in item['resultado'] else 'loss'
        diff=item['preco_expiracao']-item['preco_entrada']
        sinal_diff='+' if diff>=0 else ''
        resultado_formatado=item['resultado'].replace(' (Close)','')
        linhas.append(f"[{item['horario']}] {item['ativo']} -> <span class='{classe}'>{resultado_formatado}</span> (Assert: {item['assertividade']:.0f}%, Diff: {sinal_diff}{diff:.5f})")
    return '<br>'.join(linhas)

# ====================== ESTRATÉGIA ======================
def analisar_ativo(ativo):
    velas = get_velas_kucoin(ativo, INTERVALO_M1)
    if not velas or len(velas)<NUM_VELAS_ANALISE_M1:
        return {'ativo':ativo,'sinal':'NEUTRO 🟡','assertividade':0.0,'preco_entrada':0.0}
    preco=velas[-1][1]
    o,c,h,l = velas[-1]
    o2,c2=velas[-2][0],velas[-2][1]
    rsi=calculate_rsi(velas)
    bb=calculate_bollinger_bands(velas)
    momentum_buy=(c>o and c2>o2)
    momentum_sell=(c<o and c2<o2)
    def check_dir(direcao, momentum):
        rules=0
        if momentum: rules+=1
        else: return 0.0
        if direcao=='COMPRA' and l<=bb['lower']: rules+=1
        elif direcao=='VENDA' and h>=bb['upper']: rules+=1
        if direcao=='COMPRA' and rsi<=RSI_OVERSOLD: rules+=1
        elif direcao=='VENDA' and rsi>=RSI_OVERBOUGHT: rules+=1
        return (rules/3)*100.0
    assert_buy=check_dir('COMPRA',momentum_buy)
    assert_sell=check_dir('VENDA',momentum_sell)
    final_sinal='NEUTRO 🟡'
    final_assert=0.0
    if assert_buy>=ASSERTIVIDADE_MINIMA and assert_buy>=assert_sell:
        final_sinal='COMPRA APROVADA ✅'
        final_assert=assert_buy
    elif assert_sell>=ASSERTIVIDADE_MINIMA and assert_sell>=assert_buy:
        final_sinal='VENDA APROVADA ❌'
        final_assert=assert_sell
    else:
        final_assert=max(assert_buy,assert_sell)
        final_sinal='ENTRADA BLOQUEADA' if final_assert>0 else 'NEUTRO 🟡'
    return {'ativo':ativo,'sinal':final_sinal,'assertividade':final_assert,'preco_entrada':preco}

# ====================== CICLO DE ANÁLISE ======================
def ciclo_analise():
    global ULTIMO_SINAL, ULTIMO_SINAL_CHECAR, ULTIMO_SINAL_REGISTRADO
    time.sleep(1)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        while True:
            try:
                now_dt=get_horario_brasilia()
                sleep_time=60-now_dt.second
                if sleep_time==60: sleep_time=60
                if ULTIMO_SINAL_CHECAR:
                    checar_resultado_sinal(ULTIMO_SINAL_CHECAR)
                    with state_lock: ULTIMO_SINAL_CHECAR=None
                time.sleep(sleep_time)
                now_dt=get_horario_brasilia()
                horario_str=now_dt.strftime('%H:%M:%S')
                print(f"[{horario_str}] Iniciando ciclo...")
                sinais=list(executor.map(analisar_ativo,ATIVOS_MONITORADOS))
                melhor={'ativo':'N/A','sinal':'NEUTRO 🟡','assertividade':0.0,'preco_entrada':0.0}
                for s in sinais:
                    if s['assertividade']>=melhor['assertividade']: melhor=s
                sinal_final={'horario':horario_str,'ativo':melhor['ativo'],'sinal':melhor['sinal'],'assertividade':melhor['assertividade'],'preco_entrada':melhor['preco_entrada']}
                with state_lock:
                    if sinal_final['assertividade']>=ASSERTIVIDADE_MINIMA:
                        ULTIMO_SINAL_CHECAR=copy.deepcopy(sinal_final)
                        ULTIMO_SINAL_REGISTRADO.update({'horario':sinal_final['horario'],'sinal_tipo':'COMPRA' if 'COMPRA' in sinal_final['sinal'] else 'VENDA'})
                    ULTIMO_SINAL.update(sinal_final)
                print(f"[{horario_str}] 📢 Novo Sinal: {ULTIMO_SINAL['ativo']} - {ULTIMO_SINAL['sinal']} ({ULTIMO_SINAL['assertividade']:.0f}%)")
            except Exception:
                print("Erro ciclo_analise:")
                traceback.print_exc()
                time.sleep(5)

Thread(target=ciclo_analise,daemon=True).start()

# ====================== DASHBOARD ======================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Dashboard Trader M1</title>
<style>
body{font-family:sans-serif;background:#121212;color:#fff;margin:0;padding:0;}
.header{padding:10px;text-align:center;font-size:1.5em;font-weight:bold;}
.signal-box{margin:20px;padding:15px;border-radius:10px;}
.signal-active{animation:pulse 1s infinite;box-shadow:0 0 20px #fff;}
.win{color:#0f0;}
.loss{color:#f00;}
.neutro{color:#ff0;}
.last-signal-box{border-left:5px solid;}
.data-item{margin:5px 0;}
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
    const signalBox=document.getElementById("signal");
    const lastBox=document.getElementById("last-signal");
    const histBox=document.getElementById("historico");
    let bg="#ff0", border="#ff0", text="NEUTRO";
    if(data.sinal.includes("COMPRA")){bg="#0f0"; border="#0a0"; text="COMPRA"}
    else if(data.sinal.includes("VENDA")){bg="#f00"; border="#a00"; text="VENDA"}
    signalBox.style.backgroundColor=bg;
    signalBox.style.border="2px solid "+border;
    signalBox.innerHTML=`<strong>${text}</strong><br>Ativo: ${data.ativo}<br>Assertividade: ${data.assertividade.toFixed(0)}%<br>Preço: ${data.preco_entrada.toFixed(5)}`;
    lastBox.style.borderLeft="5px solid "+border;
    lastBox.innerHTML=`Último Sinal: ${data.sinal}<br>Hora: ${data.horario}`;
    histBox.innerHTML="<h3>Histórico Simulado</h3>"+data.historico;
};
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/stream")
def stream():
    def event_stream():
        while True:
            with state_lock:
                historico=formatar_historico_html(HISTORICO_SINAIS)
                payload={'sinal':ULTIMO_SINAL['sinal'],'ativo':ULTIMO_SINAL['ativo'],'assertividade':ULTIMO_SINAL['assertividade'],'preco_entrada':ULTIMO_SINAL['preco_entrada'],'horario':ULTIMO_SINAL['horario'],'historico':historico}
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(DASHBOARD_REFRESH_RATE_SECONDS)
    return Response(event_stream(), mimetype="text/event-stream")

# ====================== RUN ======================
if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
