from flask import Flask, render_template_string
import threading
import time
from datetime import datetime, timedelta
import requests
import pytz
import os

app = Flask(__name__)

# ===================== CONFIGURAÇÕES =====================
TIMEZONE_BR = pytz.timezone("America/Sao_Paulo")
ATIVOS = ["BTC-USDT", "ETH-USDT"]
historico = []
sinal_atual = {"ativo": "-", "tipo": "-", "hora": "-", "resultado": "-"}
assertividade = 0.0  # Percentual de WIN

# ===================== FUNÇÃO DE SINAL =====================
def obter_preco(ativo):
    try:
        r = requests.get(f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={ativo}")
        return float(r.json()["data"]["price"])
    except:
        return None

def gerar_sinal(preco_atual, preco_anterior):
    if preco_atual is None or preco_anterior is None:
        return "SEM SINAL"
    diff = preco_atual - preco_anterior
    if diff > 0.02:
        return "COMPRA"
    elif diff < -0.02:
        return "VENDA"
    else:
        return "SEM SINAL"

def checar_resultado(ativo, preco_inicial, direcao):
    """Simula o resultado (WIN/LOSS) após 10s"""
    time.sleep(10)
    preco_final = obter_preco(ativo)
    if not preco_final:
        return "SEM DADOS"
    if direcao == "COMPRA" and preco_final > preco_inicial:
        return "WIN"
    elif direcao == "VENDA" and preco_final < preco_inicial:
        return "WIN"
    else:
        return "LOSS"

def atualizar_assertividade():
    global assertividade
    total = len(historico)
    if total == 0:
        assertividade = 0.0
    else:
        wins = sum(1 for h in historico if h["resultado"] == "WIN")
        assertividade = round((wins / total) * 100, 1)

# ===================== LOOP PRINCIPAL =====================
def atualizar_robô():
    precos_anteriores = {a: None for a in ATIVOS}
    global sinal_atual
    while True:
        agora = datetime.now(TIMEZONE_BR)
        segundos = agora.second
        # Gera sinal 5s antes da nova vela
        if segundos >= 55:
            for ativo in ATIVOS:
                preco = obter_preco(ativo)
                sinal = gerar_sinal(preco, precos_anteriores[ativo])
                hora = agora.strftime("%H:%M:%S")

                if sinal != "SEM SINAL":
                    resultado = checar_resultado(ativo, preco, sinal)
                    historico.insert(0, {
                        "ativo": ativo,
                        "sinal": sinal,
                        "hora": hora,
                        "resultado": resultado
                    })
                    sinal_atual = {"ativo": ativo, "tipo": sinal, "hora": hora, "resultado": resultado}
                    atualizar_assertividade()

                precos_anteriores[ativo] = preco
            # Aguarda 6s para não gerar múltiplos sinais na mesma vela
            time.sleep(6)
        else:
            time.sleep(0.5)

# ===================== DASHBOARD =====================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<title>Robô Trader M1 - Dashboard</title>
<style>
body {
    background: radial-gradient(circle at top left, #0f2027, #203a43, #2c5364);
    color: white;
    font-family: 'Segoe UI', sans-serif;
    text-align: center;
    padding: 20px;
}
.card {
    background: rgba(255,255,255,0.08);
    border-radius: 20px;
    padding: 20px;
    margin: 20px auto;
    width: 90%;
    box-shadow: 0 0 10px rgba(0,0,0,0.5);
}
h1 { color: #00ffff; }
.sinal-compra { color: #00ff00; font-weight: bold; }
.sinal-venda { color: #ff5555; font-weight: bold; }
.sinal-sem { color: #ffff00; font-weight: bold; }
table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 20px;
}
th, td {
    padding: 10px;
    border-bottom: 1px solid rgba(255,255,255,0.1);
}
.win { color: #00ff7f; font-weight: bold; }
.loss { color: #ff4444; font-weight: bold; }
footer {
    margin-top: 30px;
    font-size: 13px;
    color: #aaa;
}
.assertividade, .ultimo-sinal {
    font-size: 18px;
    margin-bottom: 10px;
    color: #00ffff;
    font-weight: bold;
}
</style>
<script>
async function atualizarPainel(){
    const r = await fetch('/dados');
    const data = await r.json();

    document.getElementById('ativo').innerText = data.ativo;
    document.getElementById('tipo').innerText = data.tipo;
    document.getElementById('hora').innerText = data.hora;
    document.getElementById('tipo').className =
        data.tipo === "COMPRA" ? "sinal-compra" :
        data.tipo === "VENDA" ? "sinal-venda" : "sinal-sem";

    document.getElementById('assertividade').innerText = "Assertividade: " + data.assertividade + "% WIN";
    document.getElementById('ultimo-sinal').innerText = 
        data.sinal_atual_text;

    let histHTML = "";
    data.historico.forEach(h=>{
        histHTML += `<tr>
            <td>${h.ativo}</td>
            <td>${h.sinal}</td>
            <td>${h.hora}</td>
            <td class="${h.resultado=="WIN"?"win":"loss"}">${h.resultado}</td>
        </tr>`;
    });
    document.getElementById('historico').innerHTML = histHTML;
}
setInterval(atualizarPainel, 3000);
</script>
</head>
<body>
    <div class="card">
        <h1>🤖 Robô Trader M1</h1>
        <div class="assertividade" id="assertividade">Assertividade: 0% WIN</div>
        <div class="ultimo-sinal" id="ultimo-sinal">Último Sinal: -</div>
        <p>Ativo: <span id="ativo">-</span></p>
        <p>Sinal Atual: <span id="tipo">-</span></p>
        <p>Hora: <span id="hora">-</span></p>
    </div>

    <div class="card">
        <h2>📊 Histórico de Trades</h2>
        <table>
            <thead><tr><th>Ativo</th><th>Sinal</th><th>Hora</th><th>Resultado</th></tr></thead>
            <tbody id="historico"></tbody>
        </table>
    </div>

    <footer>© 2025 Polarium Broker | Desenvolvido com GPT-5 e Henrique Dos Reis Nascimento</footer>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/dados")
def dados():
    if sinal_atual["ativo"] != "-":
        sinal_text = f'Último Sinal: {sinal_atual["tipo"]} às {sinal_atual["hora"]} → {sinal_atual["resultado"]}'
    else:
        sinal_text = "Último Sinal: -"
    return {
        "ativo": sinal_atual["ativo"],
        "tipo": sinal_atual["tipo"],
        "hora": sinal_atual["hora"],
        "historico": historico[:10],
        "assertividade": assertividade,
        "sinal_atual_text": sinal_text
    }

# ===================== THREAD =====================
t = threading.Thread(target=atualizar_robô, daemon=True)
t.start()

# ===================== EXECUÇÃO =====================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
