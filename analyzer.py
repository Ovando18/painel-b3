"""
analyzer.py — Indicadores quantitativos, ranking diário (Top 1 por categoria e perfil),
análise de longo prazo e motor de decisão líquida (após IR) para cada posição.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

import tax_calculator as tc

PERFIS = ("Conservador / Seguro", "Arrojado / Maior Risco")
CATEGORIAS = {
    "ACAO_SIMPLES": "📈 Ação Simples",
    "ACAO_DIVIDENDOS": "💰 Ação de Dividendos",
    "FII_DIVIDENDOS": "🏢 FII de Dividendos",
}

# Pesos por categoria × perfil (aplicados sobre ranks percentuais dentro da categoria)
PESOS = {
    ("ACAO_SIMPLES", "C"): {"ret_12m": .25, "baixa_vol": .30, "baixo_dd": .25, "tendencia": .20},
    ("ACAO_SIMPLES", "A"): {"ret_3m": .40, "ret_12m": .30, "tendencia": .20, "folga_rsi": .10},
    ("ACAO_DIVIDENDOS", "C"): {"dy": .40, "baixa_vol": .25, "baixo_dd": .20, "tendencia": .15},
    ("ACAO_DIVIDENDOS", "A"): {"dy": .50, "ret_12m": .25, "ret_3m": .15, "tendencia": .10},
    ("FII_DIVIDENDOS", "C"): {"dy": .45, "baixa_vol": .30, "baixo_dd": .25},
    ("FII_DIVIDENDOS", "A"): {"dy": .55, "ret_12m": .25, "ret_3m": .20},
}
# Parâmetros de saída por perfil: alvo de lucro e stop
SAIDA = {"C": {"alvo": 0.20, "stop": -0.10}, "A": {"alvo": 0.35, "stop": -0.18}}


def perfil_cod(perfil: str) -> str:
    return "C" if perfil.startswith("Conservador") else "A"


# ------------------------------------------------------------------ indicadores
def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    ganho = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    perda = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = ganho / perda.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def adicionar_medias(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for n in (21, 50, 200):
        out[f"MM{n}"] = out["Close"].rolling(n).mean()
    return out


def metricas(df: pd.DataFrame) -> dict:
    close = df["Close"].dropna()
    adj = df.get("Adj Close", close).dropna()
    preco = float(close.iloc[-1])

    def ret(n):
        return float(adj.iloc[-1] / adj.iloc[-n] - 1) if len(adj) > n else np.nan

    ult = adj.iloc[-252:]
    lr = np.log(ult).diff().dropna()
    vol = float(lr.std() * np.sqrt(252)) if len(lr) > 20 else np.nan
    dd = float((ult / ult.cummax() - 1).min())
    corte = df.index[-1] - pd.Timedelta(days=365)
    div12 = float(df.loc[df.index > corte, "Dividends"].sum()) if "Dividends" in df else 0.0
    mm = {n: float(close.rolling(n).mean().iloc[-1]) if len(close) >= n else np.nan for n in (21, 50, 200)}
    if preco > mm[50] > mm[200]:
        tendencia = "Alta"
    elif preco < mm[50] < mm[200]:
        tendencia = "Baixa"
    else:
        tendencia = "Lateral"
    anos = (df.index[-1] - df.index[0]).days / 365.25
    cagr = float((adj.iloc[-1] / adj.iloc[0]) ** (1 / anos) - 1) if anos > 0.5 else np.nan
    return {"preco": preco, "ret_1m": ret(21), "ret_3m": ret(63), "ret_12m": ret(252), "vol": vol,
            "max_dd": dd, "dy": div12 / preco if preco else 0.0, "div_12m": div12,
            "mm21": mm[21], "mm50": mm[50], "mm200": mm[200], "tendencia": tendencia,
            "rsi": float(rsi(close).iloc[-1]), "cagr": cagr, "anos": anos}


# ------------------------------------------------------------------ ranking
def ranquear(metricas_por_ticker: dict[str, dict], categoria: str, perfil: str) -> pd.DataFrame:
    df = pd.DataFrame(metricas_por_ticker).T
    if df.empty:
        return df
    p = perfil_cod(perfil)
    f = pd.DataFrame(index=df.index)
    f["ret_12m"] = df["ret_12m"].astype(float).rank(pct=True)
    f["ret_3m"] = df["ret_3m"].astype(float).rank(pct=True)
    f["baixa_vol"] = (-df["vol"].astype(float)).rank(pct=True)
    f["baixo_dd"] = df["max_dd"].astype(float).rank(pct=True)  # dd menos negativo = melhor
    f["dy"] = df["dy"].astype(float).rank(pct=True)
    f["tendencia"] = df["tendencia"].map({"Alta": 1.0, "Lateral": 0.5, "Baixa": 0.0})
    f["folga_rsi"] = (df["rsi"].astype(float) < 70).astype(float)
    pesos = PESOS[(categoria, p)]
    df["score"] = sum(f[k].fillna(0) * w for k, w in pesos.items()) * 100
    # Filtros de risco do perfil conservador (com fallback se esvaziar)
    if p == "C":
        filtro = (df["rsi"].astype(float) < 75) & (df["tendencia"] != "Baixa")
        if filtro.any():
            df.loc[~filtro, "score"] -= 100
    return df.sort_values("score", ascending=False)


def analise_longo_prazo(categoria: str, m: dict, fund: dict | None = None) -> list[str]:
    fund = fund or {}
    pct = lambda x: "n/d" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:,.1f}%"
    linhas = [
        f"**Retorno anualizado (CAGR, {m['anos']:.1f} anos):** {pct(m['cagr'])} | "
        f"**12 meses:** {pct(m['ret_12m'])} | **3 meses:** {pct(m['ret_3m'])}",
        f"**Dividend Yield 12m:** {pct(m['dy'])} ({tc.brl(m['div_12m'])} por cota)",
        f"**Volatilidade anual:** {pct(m['vol'])} | **Drawdown máx. 12m:** {pct(m['max_dd'])} | "
        f"**RSI(14):** {m['rsi']:.0f}",
        f"**Tendência (preço × MM50 × MM200):** {m['tendencia']}",
    ]
    if fund.get("trailingPE") or fund.get("priceToBook"):
        linhas.append(f"**P/L:** {fund.get('trailingPE') or 'n/d'} | **P/VP:** {fund.get('priceToBook') or 'n/d'}"
                      + (f" | **Setor:** {fund['sector']}" if fund.get("sector") else ""))
    tese = {
        "ACAO_SIMPLES": "Tese de **valorização de capital**: acompanhar a manutenção da tendência "
                        "acima da MM200; horizonte sugerido de 12 a 36 meses.",
        "ACAO_DIVIDENDOS": "Tese de **renda passiva + valorização**: reinvestir proventos e "
                           "monitorar payout e previsibilidade de lucros; horizonte de 3 a 5+ anos.",
        "FII_DIVIDENDOS": "Tese de **renda mensal isenta de IR para PF** (rendimentos), observar "
                          "vacância, P/VP e qualidade dos inquilinos; ganho de capital na venda é "
                          "tributado a 20%.",
    }[categoria]
    linhas.append(tese)
    return linhas


# ------------------------------------------------------------------ decisão por posição
@dataclass
class Diagnostico:
    ticker: str
    tipo_ativo: str
    quantidade: int
    preco_medio: float
    preco_atual: float
    valor_investido: float
    valor_hoje: float
    lucro_bruto: float
    retorno_pct: float
    ir_vender_tudo: float
    lucro_liquido: float
    margem_isenta: float
    qtd_max_isenta: int
    valor_venda_isenta: float
    lucro_venda_isenta: float
    veredito: str
    motivos: list[str]
    simulacao_total: tc.Simulacao


def diagnosticar(pos: dict, preco_atual: float, hist: pd.DataFrame, vendas_hist: pd.DataFrame,
                 perfil: str, data_ref: str) -> Diagnostico:
    qtd, pm, tipo = int(pos["quantidade"]), float(pos["preco_pago"]), pos["tipo_ativo"]
    investido, hoje = qtd * pm, qtd * preco_atual
    lucro = hoje - investido
    retorno = lucro / investido if investido else 0.0

    sim = tc.simular_venda(vendas_hist, tipo, data_ref, qtd, preco_atual, pm)
    ir_total = max(0.0, sim.ir_incremental)
    margem = sim.margem_isenta_antes if tipo == tc.ACAO else 0.0
    q_isenta = tc.qtd_maxima_isenta(sim.vendas_acoes_antes, preco_atual, qtd) if tipo == tc.ACAO else 0

    p = SAIDA[perfil_cod(perfil)]
    m = metricas(hist) if hist is not None and len(hist) > 30 else None
    motivos, vender = [], False
    if retorno >= p["alvo"]:
        vender = True
        motivos.append(f"Alvo de lucro do perfil atingido ({retorno:.1%} ≥ {p['alvo']:.0%}).")
    if retorno <= p["stop"]:
        vender = True
        motivos.append(f"Stop de proteção acionado ({retorno:.1%} ≤ {p['stop']:.0%}).")
    if m:
        if m["tendencia"] == "Baixa" and lucro > 0:
            vender = True
            motivos.append("Tendência virou de baixa (preço < MM50 < MM200) — proteger o lucro.")
        if m["rsi"] > 75 and lucro > 0:
            vender = True
            motivos.append(f"Ativo sobrecomprado (RSI {m['rsi']:.0f}) — boa janela de realização.")
        if not vender:
            motivos.append(f"Tendência {m['tendencia'].lower()}, RSI {m['rsi']:.0f}, retorno {retorno:.1%}: "
                           "sem gatilho de saída para o perfil.")

    if vender and tipo == tc.ACAO and lucro > 0 and ir_total > 0 and q_isenta >= 1:
        veredito = "VENDA PARCIAL ISENTA"
        motivos.append(f"Vender tudo hoje geraria {tc.brl(ir_total)} de IR; vendendo {q_isenta} cotas "
                       f"({tc.brl(q_isenta * preco_atual)}) você realiza lucro sem estourar o teto mensal.")
    elif vender:
        veredito = "VENDER AGORA"
    else:
        veredito = "MANTER EM CARTEIRA"

    return Diagnostico(
        ticker=pos["ticker"], tipo_ativo=tipo, quantidade=qtd, preco_medio=pm, preco_atual=preco_atual,
        valor_investido=round(investido, 2), valor_hoje=round(hoje, 2), lucro_bruto=round(lucro, 2),
        retorno_pct=retorno, ir_vender_tudo=round(ir_total, 2), lucro_liquido=round(lucro - ir_total, 2),
        margem_isenta=margem, qtd_max_isenta=q_isenta, valor_venda_isenta=round(q_isenta * preco_atual, 2),
        lucro_venda_isenta=round(q_isenta * (preco_atual - pm), 2), veredito=veredito, motivos=motivos,
        simulacao_total=sim)
