"""
tax_calculator.py — Apuração de IR sobre ganho de capital em Renda Variável (B3)
para operações comuns (Swing Trade), segundo as regras vigentes da Receita Federal.

Regras implementadas
--------------------
AÇÕES (mercado à vista, operações comuns):
  * Isenção: se o TOTAL DE ALIENAÇÕES (valor bruto de vendas) de ações no mês civil
    for <= R$ 20.000,00, o ganho líquido do mês é isento (Lei 11.033/2004, art. 3º, I;
    IN RFB 1.585/2015, art. 59).
  * Ultrapassado o teto, TODO o ganho líquido do mês é tributado a 15% — inclusive o
    das vendas feitas antes de o teto ser estourado (efeito "retroativo" no mês).
  * Prejuízos podem ser compensados com ganhos líquidos de meses tributáveis
    subsequentes. Prejuízo apurado em mês isento também é acumulável; ganho de mês
    isento NÃO consome o prejuízo acumulado.

FIIs (cotas de Fundos Imobiliários):
  * Não há isenção de R$ 20 mil; ganho líquido tributado a 20%.
  * Vendas de FIIs NÃO entram no somatório do teto de R$ 20 mil das ações.
  * Prejuízos de FII só compensam ganhos de FII (pool separado).

DARF (código 6015): vencimento no último dia útil do mês seguinte. Valores abaixo
de R$ 10,00 não são recolhidos — acumulam para os meses seguintes.

Este módulo é PURO (sem I/O): recebe vendas e devolve a apuração. Isso permite
recalcular retroativamente todo o histórico a cada lançamento/estorno.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

import pandas as pd

LIMITE_ISENCAO_ACOES = 20_000.00
ALIQUOTA_ACOES = 0.15
ALIQUOTA_FII = 0.20
DARF_MINIMO = 10.00

ACAO = "ACAO"
FII = "FII"


def _r2(x: float) -> float:
    return float(round(x + 0.0, 2))


def mes_referencia(data_iso: str | date) -> str:
    """'2026-11-14' -> '2026-11'."""
    if isinstance(data_iso, date):
        return data_iso.strftime("%Y-%m")
    return str(data_iso)[:7]


def ultimo_dia_util_mes_seguinte(mes: str) -> date:
    """Vencimento do DARF (desconsidera feriados — apoio gerencial)."""
    ano, m = map(int, mes.split("-"))
    ano, m = (ano + 1, 1) if m == 12 else (ano, m + 1)
    d = date(ano, m, calendar.monthrange(ano, m)[1])
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


@dataclass
class ApuracaoMes:
    mes: str
    # Ações
    vendas_acoes: float = 0.0
    resultado_acoes: float = 0.0
    isento_acoes: bool = True
    prejuizo_compensado_acoes: float = 0.0
    base_calculo_acoes: float = 0.0
    ir_acoes: float = 0.0
    prejuizo_acumulado_acoes: float = 0.0  # saldo ao FINAL do mês
    # FIIs
    vendas_fii: float = 0.0
    resultado_fii: float = 0.0
    prejuizo_compensado_fii: float = 0.0
    base_calculo_fii: float = 0.0
    ir_fii: float = 0.0
    prejuizo_acumulado_fii: float = 0.0
    # DARF
    ir_total_mes: float = 0.0
    ir_residual_anterior: float = 0.0  # < R$ 10 acumulado de meses anteriores
    darf_a_pagar: float = 0.0
    ir_residual_proximo: float = 0.0
    vencimento_darf: date | None = None
    alocacao: dict = field(default_factory=dict)  # venda_id -> (ir, isento)


def _normalizar(vendas: pd.DataFrame | Iterable[dict]) -> pd.DataFrame:
    df = pd.DataFrame(vendas).copy() if not isinstance(vendas, pd.DataFrame) else vendas.copy()
    if df.empty:
        return pd.DataFrame(columns=["id", "tipo_ativo", "mes_referencia",
                                     "valor_total_venda", "lucro_prejuizo_bruto"])
    if "mes_referencia" not in df.columns or df["mes_referencia"].isna().any():
        df["mes_referencia"] = df["data_venda"].map(mes_referencia)
    if "id" not in df.columns:
        df["id"] = range(1, len(df) + 1)
    df["valor_total_venda"] = df["valor_total_venda"].astype(float)
    df["lucro_prejuizo_bruto"] = df["lucro_prejuizo_bruto"].astype(float)
    return df


def _alocar(ir_mes: float, vendas_mes: pd.DataFrame) -> dict:
    """Rateia o IR do mês entre as vendas com lucro, proporcional ao lucro de cada uma."""
    positivas = vendas_mes[vendas_mes["lucro_prejuizo_bruto"] > 0]
    soma = positivas["lucro_prejuizo_bruto"].sum()
    out = {int(i): 0.0 for i in vendas_mes["id"]}
    if ir_mes <= 0 or soma <= 0:
        return out
    restante = _r2(ir_mes)
    ids = list(positivas["id"])
    for k, (vid, lucro) in enumerate(zip(ids, positivas["lucro_prejuizo_bruto"])):
        parte = restante if k == len(ids) - 1 else _r2(ir_mes * lucro / soma)
        out[int(vid)] = parte
        restante = _r2(restante - parte)
    return out


def apurar(vendas: pd.DataFrame | Iterable[dict],
           prejuizo_inicial_acoes: float = 0.0,
           prejuizo_inicial_fii: float = 0.0) -> tuple[pd.DataFrame, list[ApuracaoMes]]:
    """
    Apura cronologicamente TODOS os meses do histórico.

    Retorna (vendas_com_tributo, apuracoes_mensais). Em `vendas_com_tributo`, as
    colunas `ir_devido` e `isento` são recalculadas do zero — é o recálculo
    retroativo exigido quando uma nova venda estoura o teto de R$ 20 mil no mês.
    """
    df = _normalizar(vendas)
    meses: list[ApuracaoMes] = []
    pool_acoes, pool_fii, residual = float(prejuizo_inicial_acoes), float(prejuizo_inicial_fii), 0.0
    df["ir_devido"] = 0.0
    df["isento"] = False

    for mes in sorted(df["mes_referencia"].unique()) if not df.empty else []:
        ap = ApuracaoMes(mes=mes)
        vm = df[df["mes_referencia"] == mes]
        acoes = vm[vm["tipo_ativo"] == ACAO]
        fiis = vm[vm["tipo_ativo"] == FII]

        # ---------- AÇÕES ----------
        ap.vendas_acoes = _r2(acoes["valor_total_venda"].sum())
        ap.resultado_acoes = _r2(acoes["lucro_prejuizo_bruto"].sum())
        ap.isento_acoes = ap.vendas_acoes <= LIMITE_ISENCAO_ACOES
        if ap.resultado_acoes < 0:
            pool_acoes += -ap.resultado_acoes            # prejuízo sempre acumula
        elif not ap.isento_acoes:
            comp = min(pool_acoes, ap.resultado_acoes)
            pool_acoes -= comp
            ap.prejuizo_compensado_acoes = _r2(comp)
            ap.base_calculo_acoes = _r2(ap.resultado_acoes - comp)
            ap.ir_acoes = _r2(ap.base_calculo_acoes * ALIQUOTA_ACOES)
        ap.prejuizo_acumulado_acoes = _r2(pool_acoes)

        # ---------- FIIs ----------
        ap.vendas_fii = _r2(fiis["valor_total_venda"].sum())
        ap.resultado_fii = _r2(fiis["lucro_prejuizo_bruto"].sum())
        if ap.resultado_fii < 0:
            pool_fii += -ap.resultado_fii
        elif ap.resultado_fii > 0:
            comp = min(pool_fii, ap.resultado_fii)
            pool_fii -= comp
            ap.prejuizo_compensado_fii = _r2(comp)
            ap.base_calculo_fii = _r2(ap.resultado_fii - comp)
            ap.ir_fii = _r2(ap.base_calculo_fii * ALIQUOTA_FII)
        ap.prejuizo_acumulado_fii = _r2(pool_fii)

        # ---------- DARF ----------
        ap.ir_total_mes = _r2(ap.ir_acoes + ap.ir_fii)
        ap.ir_residual_anterior = _r2(residual)
        total = _r2(ap.ir_total_mes + residual)
        if total >= DARF_MINIMO:
            ap.darf_a_pagar, residual = total, 0.0
        else:
            ap.darf_a_pagar, residual = 0.0, total
        ap.ir_residual_proximo = _r2(residual)
        ap.vencimento_darf = ultimo_dia_util_mes_seguinte(mes)

        # ---------- Rateio por venda ----------
        aloc = {}
        for vid, ir in _alocar(ap.ir_acoes, acoes).items():
            aloc[vid] = (ir, bool(ap.isento_acoes))
        for vid, ir in _alocar(ap.ir_fii, fiis).items():
            aloc[vid] = (ir, False)
        ap.alocacao = aloc
        for vid, (ir, isento) in aloc.items():
            idx = df.index[df["id"] == vid]
            df.loc[idx, "ir_devido"] = ir
            df.loc[idx, "isento"] = isento
        meses.append(ap)

    return df, meses


def apuracao_do_mes(apuracoes: list[ApuracaoMes], mes: str) -> ApuracaoMes:
    """Apuração de um mês; se não houver vendas, devolve um mês vazio que carrega os
    saldos de prejuízo acumulado do último mês anterior apurado."""
    anteriores = [a for a in apuracoes if a.mes <= mes]
    for a in apuracoes:
        if a.mes == mes:
            return a
    vazio = ApuracaoMes(mes=mes, vencimento_darf=ultimo_dia_util_mes_seguinte(mes))
    if anteriores:
        ult = anteriores[-1]
        vazio.prejuizo_acumulado_acoes = ult.prejuizo_acumulado_acoes
        vazio.prejuizo_acumulado_fii = ult.prejuizo_acumulado_fii
        vazio.ir_residual_anterior = ult.ir_residual_proximo
    return vazio


@dataclass
class Simulacao:
    mes: str
    tipo_ativo: str
    valor_venda: float
    lucro_venda: float
    vendas_acoes_antes: float
    vendas_acoes_depois: float
    isento_antes: bool
    isento_depois: bool
    estoura_teto: bool
    ir_mes_antes: float
    ir_mes_depois: float
    ir_incremental: float  # efeito total no IR (inclui retroativo e compensações)
    margem_isenta_antes: float
    mensagem: str


def margem_isenta(vendas_acoes_mes: float) -> float:
    return _r2(max(0.0, LIMITE_ISENCAO_ACOES - vendas_acoes_mes))


def qtd_maxima_isenta(vendas_acoes_mes: float, preco: float, qtd_disponivel: int) -> int:
    """Quantidade máxima de cotas vendáveis sem ultrapassar o teto (venda == teto é isenta)."""
    if preco <= 0:
        return 0
    m = margem_isenta(vendas_acoes_mes)
    q = int((m + 1e-9) // preco)
    while q > 0 and q * preco > m + 1e-9:  # proteção contra arredondamento
        q -= 1
    return max(0, min(q, int(qtd_disponivel)))


def simular_venda(vendas: pd.DataFrame | Iterable[dict], tipo_ativo: str, data_venda: str | date,
                  quantidade: int, preco_venda: float, preco_medio: float) -> Simulacao:
    """Simula o impacto tributário de uma nova venda ANTES de confirmá-la."""
    base = _normalizar(vendas)
    mes = mes_referencia(data_venda)
    valor = _r2(quantidade * preco_venda)
    lucro = _r2(quantidade * (preco_venda - preco_medio))
    nova = {"id": -1, "tipo_ativo": tipo_ativo, "data_venda": str(data_venda), "mes_referencia": mes,
            "valor_total_venda": valor, "lucro_prejuizo_bruto": lucro}
    depois_df = pd.concat([base, pd.DataFrame([nova])], ignore_index=True) if not base.empty \
        else pd.DataFrame([nova])

    _, ap_antes = apurar(base)
    _, ap_depois = apurar(depois_df)
    a0, a1 = apuracao_do_mes(ap_antes, mes), apuracao_do_mes(ap_depois, mes)
    ir_tot_antes = sum(a.ir_total_mes for a in ap_antes)
    ir_tot_depois = sum(a.ir_total_mes for a in ap_depois)
    ir_inc = _r2(ir_tot_depois - ir_tot_antes)

    estoura = tipo_ativo == ACAO and a0.isento_acoes and not a1.isento_acoes
    if tipo_ativo == FII:
        msg = (f"Venda de FII de {brl(valor)} (FIIs não têm isenção nem entram no teto de R$ 20 mil). "
               + (f"Gera {brl(ir_inc)} de IR (20%) a pagar via DARF." if ir_inc > 0
                  else "Não gera IR a pagar (sem ganho líquido tributável após compensações)."))
    elif a1.isento_acoes:
        msg = (f"Esta venda de {brl(valor)} elevará seu total mensal de ações para "
               f"{brl(a1.vendas_acoes)} — continua 100% ISENTO de IR.")
    else:
        prefixo = ("Esta venda ULTRAPASSARÁ R$ 20.000,00 no mês" if estoura
                   else "O teto de R$ 20.000,00 do mês já está ultrapassado")
        msg = (f"{prefixo} (total de ações: {brl(a1.vendas_acoes)}), "
               + (f"gerando {brl(ir_inc)} de IR a pagar via DARF (15% sobre o ganho líquido do mês"
                  + (", inclusive vendas anteriores do mês" if estoura else "") + ")."
                  if ir_inc > 0 else "mas não gera IR adicional (ganho líquido do mês não positivo "
                                     "ou compensado por prejuízos acumulados)."))
    return Simulacao(mes=mes, tipo_ativo=tipo_ativo, valor_venda=valor, lucro_venda=lucro,
                     vendas_acoes_antes=a0.vendas_acoes, vendas_acoes_depois=a1.vendas_acoes,
                     isento_antes=a0.isento_acoes, isento_depois=a1.isento_acoes, estoura_teto=estoura,
                     ir_mes_antes=a0.ir_total_mes, ir_mes_depois=a1.ir_total_mes, ir_incremental=ir_inc,
                     margem_isenta_antes=margem_isenta(a0.vendas_acoes), mensagem=msg)


def brl(v: float) -> str:
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-R$ {s}" if v < 0 else f"R$ {s}"
