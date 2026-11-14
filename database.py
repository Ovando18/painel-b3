"""
database.py — Persistência local em SQLite (carteira.db).

Tabelas:
  * posicoes_ativas   — uma linha por ticker (preço médio ponderado, regra da RFB).
  * historico_vendas  — cada alienação; `ir_devido`/`isento` são recalculados
                        retroativamente a cada inserção/estorno via tax_calculator.apurar().
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime

import pandas as pd

import tax_calculator as tc

DB_PATH = os.environ.get(
    "CARTEIRA_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "carteira.db")
)

TIPOS = ("ACAO", "FII")
ORIGENS_COMPRA = ("Recomendação do Dia", "Manual")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posicoes_ativas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT    NOT NULL UNIQUE,
    tipo_ativo   TEXT    NOT NULL CHECK (tipo_ativo IN ('ACAO','FII')),
    data_compra  TEXT    NOT NULL,
    quantidade   INTEGER NOT NULL CHECK (quantidade > 0),
    preco_pago   REAL    NOT NULL CHECK (preco_pago > 0),
    origem       TEXT    NOT NULL CHECK (origem IN ('Recomendação do Dia','Manual'))
);

CREATE TABLE IF NOT EXISTS historico_vendas (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker                TEXT    NOT NULL,
    tipo_ativo            TEXT    NOT NULL CHECK (tipo_ativo IN ('ACAO','FII')),
    data_venda            TEXT    NOT NULL,          -- YYYY-MM-DD
    mes_referencia        TEXT    NOT NULL,          -- YYYY-MM
    quantidade_vendida    INTEGER NOT NULL CHECK (quantidade_vendida > 0),
    preco_medio_compra    REAL    NOT NULL,
    preco_venda           REAL    NOT NULL CHECK (preco_venda > 0),
    valor_total_venda     REAL    NOT NULL,
    lucro_prejuizo_bruto  REAL    NOT NULL,
    ir_devido             REAL    NOT NULL DEFAULT 0,
    isento                INTEGER NOT NULL DEFAULT 1, -- booleano
    -- metadados auxiliares (estorno / vendas externas)
    origem_venda          TEXT    NOT NULL DEFAULT 'Painel' CHECK (origem_venda IN ('Painel','Externa')),
    data_compra_original  TEXT,
    origem_compra         TEXT,
    registrado_em         TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_vendas_mes ON historico_vendas (mes_referencia);
"""


@contextmanager
def conexao(path: str | None = None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: str | None = None) -> None:
    with conexao(path) as c:
        c.executescript(_SCHEMA)


def _iso(d) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]


# ------------------------------------------------------------------ posições
def listar_posicoes(path: str | None = None) -> pd.DataFrame:
    with conexao(path) as c:
        return pd.read_sql_query("SELECT * FROM posicoes_ativas ORDER BY data_compra, ticker", c)


def obter_posicao(ticker: str, path: str | None = None) -> dict | None:
    with conexao(path) as c:
        r = c.execute("SELECT * FROM posicoes_ativas WHERE ticker = ?", (ticker.upper(),)).fetchone()
        return dict(r) if r else None


def _somar_posicao(c: sqlite3.Connection, ticker: str, tipo: str, data_compra: str,
                   quantidade: int, preco: float, origem: str) -> int:
    """Insere ou incorpora à posição existente com preço médio ponderado."""
    r = c.execute("SELECT * FROM posicoes_ativas WHERE ticker = ?", (ticker,)).fetchone()
    if r is None:
        cur = c.execute(
            "INSERT INTO posicoes_ativas (ticker, tipo_ativo, data_compra, quantidade, preco_pago, origem)"
            " VALUES (?,?,?,?,?,?)", (ticker, tipo, data_compra, int(quantidade), float(preco), origem))
        return cur.lastrowid
    nova_qtd = r["quantidade"] + int(quantidade)
    pm = (r["quantidade"] * r["preco_pago"] + int(quantidade) * float(preco)) / nova_qtd
    c.execute("UPDATE posicoes_ativas SET quantidade=?, preco_pago=?, data_compra=? WHERE id=?",
              (nova_qtd, round(pm, 6), min(r["data_compra"], data_compra), r["id"]))
    return r["id"]


def registrar_compra(ticker: str, tipo_ativo: str, data_compra, quantidade: int, preco_pago: float,
                     origem: str = "Manual", path: str | None = None) -> int:
    ticker = ticker.upper().strip()
    if tipo_ativo not in TIPOS:
        raise ValueError(f"tipo_ativo inválido: {tipo_ativo}")
    if origem not in ORIGENS_COMPRA:
        raise ValueError(f"origem inválida: {origem}")
    if int(quantidade) <= 0 or float(preco_pago) <= 0:
        raise ValueError("Quantidade e preço devem ser positivos.")
    with conexao(path) as c:
        return _somar_posicao(c, ticker, tipo_ativo, _iso(data_compra), int(quantidade),
                              float(preco_pago), origem)


def excluir_posicao(posicao_id: int, path: str | None = None) -> None:
    with conexao(path) as c:
        c.execute("DELETE FROM posicoes_ativas WHERE id = ?", (posicao_id,))


# ------------------------------------------------------------------ vendas
def listar_vendas(mes: str | None = None, path: str | None = None) -> pd.DataFrame:
    with conexao(path) as c:
        if mes:
            df = pd.read_sql_query("SELECT * FROM historico_vendas WHERE mes_referencia = ?"
                                   " ORDER BY data_venda, id", c, params=(mes,))
        else:
            df = pd.read_sql_query("SELECT * FROM historico_vendas ORDER BY data_venda, id", c)
    df["isento"] = df["isento"].astype(bool)
    return df


def _recalcular(c: sqlite3.Connection) -> None:
    """Recálculo retroativo de IR/isenção de TODO o histórico (regra mensal da RFB)."""
    df = pd.read_sql_query("SELECT id, tipo_ativo, data_venda, mes_referencia, valor_total_venda,"
                           " lucro_prejuizo_bruto FROM historico_vendas", c)
    if df.empty:
        return
    res, _ = tc.apurar(df)
    c.executemany("UPDATE historico_vendas SET ir_devido = ?, isento = ? WHERE id = ?",
                  [(float(r.ir_devido), int(bool(r.isento)), int(r.id)) for r in res.itertuples()])


def recalcular_tributos(path: str | None = None) -> None:
    with conexao(path) as c:
        _recalcular(c)


def _inserir_venda(c, ticker, tipo, data_venda, qtd, pm, preco_venda, origem_venda,
                   data_compra_original=None, origem_compra=None) -> int:
    valor = round(int(qtd) * float(preco_venda), 2)
    lucro = round(int(qtd) * (float(preco_venda) - float(pm)), 2)
    cur = c.execute(
        """INSERT INTO historico_vendas (ticker, tipo_ativo, data_venda, mes_referencia,
               quantidade_vendida, preco_medio_compra, preco_venda, valor_total_venda,
               lucro_prejuizo_bruto, ir_devido, isento, origem_venda, data_compra_original, origem_compra)
           VALUES (?,?,?,?,?,?,?,?,?,0,1,?,?,?)""",
        (ticker, tipo, _iso(data_venda), tc.mes_referencia(_iso(data_venda)), int(qtd), float(pm),
         float(preco_venda), valor, lucro, origem_venda, data_compra_original, origem_compra))
    return cur.lastrowid


def registrar_venda(posicao_id: int, quantidade: int, preco_venda: float, data_venda,
                    path: str | None = None) -> int:
    """Venda total ou parcial de uma posição do painel (transação atômica):
    grava no histórico, baixa/remove a posição e recalcula o IR retroativamente."""
    quantidade = int(quantidade)
    if quantidade <= 0 or float(preco_venda) <= 0:
        raise ValueError("Quantidade e preço de venda devem ser positivos.")
    with conexao(path) as c:
        p = c.execute("SELECT * FROM posicoes_ativas WHERE id = ?", (posicao_id,)).fetchone()
        if p is None:
            raise ValueError("Posição não encontrada.")
        if quantidade > p["quantidade"]:
            raise ValueError(f"Quantidade ({quantidade}) maior que a posição ({p['quantidade']}).")
        if _iso(data_venda) < p["data_compra"]:
            raise ValueError("A data da venda não pode ser anterior à data de compra.")
        vid = _inserir_venda(c, p["ticker"], p["tipo_ativo"], data_venda, quantidade, p["preco_pago"],
                             preco_venda, "Painel", p["data_compra"], p["origem"])
        if quantidade == p["quantidade"]:
            c.execute("DELETE FROM posicoes_ativas WHERE id = ?", (posicao_id,))       # venda total
        else:  # venda parcial — preço médio do saldo permanece o mesmo
            c.execute("UPDATE posicoes_ativas SET quantidade = quantidade - ? WHERE id = ?",
                      (quantidade, posicao_id))
        _recalcular(c)
        return vid


def registrar_venda_externa(ticker: str, tipo_ativo: str, data_venda, quantidade: int,
                            preco_medio_compra: float, preco_venda: float, path: str | None = None) -> int:
    """Venda feita fora do painel no mês (soma ao teto; não mexe em posicoes_ativas)."""
    if tipo_ativo not in TIPOS:
        raise ValueError("tipo_ativo inválido")
    if int(quantidade) <= 0 or float(preco_venda) <= 0 or float(preco_medio_compra) < 0:
        raise ValueError("Valores inválidos.")
    with conexao(path) as c:
        vid = _inserir_venda(c, ticker.upper().strip(), tipo_ativo, data_venda, quantidade,
                             preco_medio_compra, preco_venda, "Externa")
        _recalcular(c)
        return vid


def estornar_venda(venda_id: int, path: str | None = None) -> dict:
    """Exclui um lançamento; se veio do painel, devolve a quantidade a posicoes_ativas."""
    with conexao(path) as c:
        v = c.execute("SELECT * FROM historico_vendas WHERE id = ?", (venda_id,)).fetchone()
        if v is None:
            raise ValueError("Venda não encontrada.")
        v = dict(v)
        if v["origem_venda"] == "Painel":
            _somar_posicao(c, v["ticker"], v["tipo_ativo"], v["data_compra_original"] or v["data_venda"],
                           v["quantidade_vendida"], v["preco_medio_compra"],
                           v["origem_compra"] if v["origem_compra"] in ORIGENS_COMPRA else "Manual")
        c.execute("DELETE FROM historico_vendas WHERE id = ?", (venda_id,))
        _recalcular(c)
        return v
