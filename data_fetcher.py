"""
data_fetcher.py — Cotações, histórico e proventos via yfinance (sufixo .SA da B3).

Se o Yahoo Finance estiver inacessível (rede bloqueada/limite de requisições) ou se
PAINEL_OFFLINE=1, cai automaticamente para um gerador DETERMINÍSTICO de séries
simuladas, para que o painel continue utilizável e testável. O app exibe um aviso
claro sempre que os dados forem simulados.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Universo monitorado por categoria (tickers B3 sem o sufixo .SA).
# Units (ex.: TAEE11/KLBN11) foram evitadas: a aplicação da isenção de R$ 20 mil a
# units é controversa perante a RFB.
UNIVERSO = {
    "ACAO_SIMPLES": ["PETR4", "VALE3", "ITUB4", "WEGE3", "PRIO3", "RENT3", "EMBR3", "RADL3",
                     "SUZB3", "LREN3", "B3SA3", "EQTL3", "GGBR4", "RAIL3", "TOTS3", "ABEV3"],
    "ACAO_DIVIDENDOS": ["BBAS3", "BBSE3", "CMIG4", "ITSA4", "EGIE3", "CXSE3", "ISAE4", "SAPR4",
                        "BBDC4", "TAEE4", "VBBR3", "GOAU4"],
    "FII_DIVIDENDOS": ["HGLG11", "MXRF11", "KNRI11", "XPML11", "VISC11", "BTLG11", "KNCR11",
                       "HGRU11", "CPTS11", "TRXF11", "KNIP11", "PVBI11"],
}
TIPO_POR_CATEGORIA = {"ACAO_SIMPLES": "ACAO", "ACAO_DIVIDENDOS": "ACAO", "FII_DIVIDENDOS": "FII"}

_FORCAR_OFFLINE = os.environ.get("PAINEL_OFFLINE", "0") == "1"
_offline_ate = 0.0  # após falha de rede, evita novas tentativas lentas por 10 min


def modo_offline() -> bool:
    return _FORCAR_OFFLINE or time.time() < _offline_ate


def tipo_do_ticker(ticker: str) -> str:
    t = ticker.upper()
    for cat, lst in UNIVERSO.items():
        if t in lst:
            return TIPO_POR_CATEGORIA[cat]
    return "FII" if t.endswith("11") else "ACAO"


def _yf_symbol(ticker: str) -> str:
    t = ticker.upper().strip()
    return t if t.endswith(".SA") or t.startswith("^") else f"{t}.SA"


# --------------------------------------------------------------- simulação
def _seed(ticker: str) -> int:
    return int(hashlib.sha256(ticker.upper().encode()).hexdigest()[:8], 16)


def _historico_simulado(ticker: str, anos: int = 5) -> pd.DataFrame:
    """Série GBM determinística por ticker (mesma série a cada execução no mesmo dia)."""
    rng = np.random.default_rng(_seed(ticker))
    fim = pd.Timestamp(date.today())
    idx = pd.bdate_range(end=fim, periods=252 * anos)
    tipo = tipo_do_ticker(ticker)
    if tipo == "FII":
        p0, mu, sigma, dy = rng.uniform(8, 160), rng.uniform(-0.02, 0.06), rng.uniform(0.08, 0.16), rng.uniform(0.08, 0.13)
    else:
        p0, mu, sigma = rng.uniform(8, 70), rng.uniform(-0.05, 0.22), rng.uniform(0.20, 0.45)
        dy = rng.uniform(0.06, 0.12) if ticker.upper() in UNIVERSO["ACAO_DIVIDENDOS"] else rng.uniform(0.0, 0.05)
    r = rng.normal(mu / 252 - 0.5 * sigma ** 2 / 252, sigma / np.sqrt(252), len(idx))
    close = p0 * np.exp(np.cumsum(r))
    abertura = close * (1 + rng.normal(0, sigma / np.sqrt(252) / 2, len(idx)))
    alta = np.maximum(close, abertura) * (1 + np.abs(rng.normal(0, 0.006, len(idx))))
    baixa = np.minimum(close, abertura) * (1 - np.abs(rng.normal(0, 0.006, len(idx))))
    df = pd.DataFrame({"Open": abertura, "High": alta, "Low": baixa, "Close": close,
                       "Volume": rng.integers(1e5, 5e6, len(idx))}, index=idx)
    df["Dividends"] = 0.0
    freq = 21 if tipo == "FII" else 63  # FII mensal, ação trimestral
    for i in range(10, len(idx), freq):
        df.iloc[i, df.columns.get_loc("Dividends")] = close[i] * dy / (252 / freq) * rng.uniform(0.8, 1.2)
    df["Adj Close"] = df["Close"]
    df.index.name = "Date"
    return df.round(4)


# --------------------------------------------------------------- yfinance
def _baixar_yf(ticker: str, period: str) -> pd.DataFrame | None:
    global _offline_ate
    try:
        import yfinance as yf
        df = yf.Ticker(_yf_symbol(ticker)).history(period=period, auto_adjust=False, actions=True,
                                                   timeout=10)
        if df is None or df.empty:
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        if "Adj Close" not in df.columns:
            df["Adj Close"] = df["Close"]
        if "Dividends" not in df.columns:
            df["Dividends"] = 0.0
        return df[["Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends"]]
    except Exception as e:  # rede bloqueada, rate-limit, etc.
        log.warning("yfinance indisponível para %s: %s — usando dados simulados", ticker, e)
        _offline_ate = time.time() + 600
        return None


def historico(ticker: str, period: str = "5y") -> tuple[pd.DataFrame, bool]:
    """Retorna (DataFrame OHLC + Dividends, is_live)."""
    if not modo_offline():
        df = _baixar_yf(ticker, period)
        if df is not None:
            return df, True
    anos = {"1y": 1, "2y": 2, "5y": 5}.get(period, 5)
    return _historico_simulado(ticker, anos), False


def cotacao_atual(ticker: str) -> tuple[float | None, bool]:
    df, live = historico(ticker, "5y")
    if df is None or df.empty:
        return None, live
    return float(df["Close"].iloc[-1]), live


def fundamentos(ticker: str) -> dict:
    """P/L, P/VP, setor etc. (somente online; vazio se indisponível)."""
    if modo_offline():
        return {}
    try:
        import yfinance as yf
        info = yf.Ticker(_yf_symbol(ticker)).info or {}
        return {k: info.get(k) for k in ("longName", "sector", "industry", "trailingPE", "priceToBook",
                                         "dividendYield", "returnOnEquity", "marketCap")}
    except Exception:
        return {}


def historico_desde(ticker: str, inicio: str) -> tuple[pd.DataFrame, bool]:
    df, live = historico(ticker, "5y")
    ini = pd.Timestamp(inicio) - timedelta(days=0)
    return df[df.index >= ini], live
