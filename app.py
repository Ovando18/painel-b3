"""
app.py — Painel B3: Radar de Oportunidades, Carteira, Execução de Vendas e Controle de IR.

Executar:  streamlit run app.py
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import analyzer as an
import data_fetcher as dfc
import database as db
import tax_calculator as tc

st.set_page_config(page_title="Painel B3 — Carteira & IR", page_icon="📊", layout="wide")
db.init_db()

HOJE = date.today()
DIA = HOJE.isoformat()
MES_ATUAL = HOJE.strftime("%Y-%m")
COR = {"preco": "#2a78d6", "mm21": "#eb6834", "mm50": "#1baf7a", "mm200": "#eda100",
       "div": "#6250d6", "pago": "#52514e"}
VEREDITO_UI = {"VENDER AGORA": ("🔴", "error"), "VENDA PARCIAL ISENTA": ("🟡", "warning"),
               "MANTER EM CARTEIRA": ("🟢", "success")}


# ======================================================================== cache de dados
@st.cache_data(ttl=3600, show_spinner=False)
def hist_cache(ticker: str, dia: str) -> tuple[pd.DataFrame, bool]:
    return dfc.historico(ticker, "5y")


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fund_cache(ticker: str, dia: str) -> dict:
    return dfc.fundamentos(ticker)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def recomendacoes_do_dia(perfil: str, dia: str) -> dict:
    """Top 1 diário por categoria (sem repetir ticker entre categorias)."""
    out, usados = {}, set()
    for cat, tickers in dfc.UNIVERSO.items():
        mets, live_any = {}, False
        for t in tickers:
            h, live = hist_cache(t, dia)
            live_any |= live
            if h is not None and len(h) > 260:
                mets[t] = an.metricas(h)
        rk = an.ranquear(mets, cat, perfil)
        rk = rk[~rk.index.isin(usados)] if not rk.empty else rk
        if rk.empty:
            continue
        top = rk.index[0]
        usados.add(top)
        out[cat] = {"ticker": top, "metricas": mets[top], "ranking": rk, "live": live_any}
    return out


def preco_atual(ticker: str) -> tuple[float, bool]:
    h, live = hist_cache(ticker, DIA)
    return float(h["Close"].iloc[-1]), live


# ======================================================================== gráficos
def grafico(df: pd.DataFrame, titulo: str, preco_pago: float | None = None,
            data_compra: str | None = None, altura: int = 430) -> go.Figure:
    d = an.adicionar_medias(df)
    if data_compra:
        d = d[d.index >= pd.Timestamp(data_compra) - pd.Timedelta(days=5)]
    else:
        d = d.iloc[-252:]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.76, 0.24],
                        vertical_spacing=0.06, subplot_titles=(titulo, "Proventos por cota (R$)"))
    fig.add_trace(go.Scatter(x=d.index, y=d["Close"], name="Fechamento",
                             line=dict(color=COR["preco"], width=2)), 1, 1)
    for n, dash in ((21, "dot"), (50, "dash"), (200, "solid")):
        fig.add_trace(go.Scatter(x=d.index, y=d[f"MM{n}"], name=f"MM{n}",
                                 line=dict(color=COR[f"mm{n}"], width=1.5, dash=dash)), 1, 1)
    if preco_pago:
        fig.add_hline(y=preco_pago, line=dict(color=COR["pago"], width=1.5, dash="dashdot"),
                      annotation_text=f"Preço pago {tc.brl(preco_pago)}", annotation_position="top left",
                      row=1, col=1)
    if data_compra:
        fig.add_vline(x=pd.Timestamp(data_compra), line=dict(color=COR["pago"], width=1, dash="dot"),
                      row=1, col=1)
    dv = d[d["Dividends"] > 0]
    fig.add_trace(go.Bar(x=dv.index, y=dv["Dividends"], name="Proventos", marker_color=COR["div"],
                         hovertemplate="R$ %{y:.4f}<extra>Provento</extra>"), 2, 1)
    fig.update_layout(height=altura, hovermode="x unified", margin=dict(l=10, r=10, t=40, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.04, x=0))
    fig.update_yaxes(tickprefix="R$ ", gridcolor="rgba(128,128,128,0.15)")
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.10)")
    return fig


def aviso_dados(live: bool):
    if not live:
        st.warning("⚠️ **Yahoo Finance indisponível** — exibindo **dados SIMULADOS** (determinísticos) "
                   "apenas para demonstração. Não use estas cotações para decidir operações.", icon="📡")


# ======================================================================== cabeçalho
st.title("📊 Painel B3 — Radar, Carteira & Controle de IR")
st.caption(f"Referência: {HOJE.strftime('%d/%m/%Y')} · Mês de apuração: {MES_ATUAL} · "
           f"Banco local: `{db.DB_PATH}`")
aba1, aba2 = st.tabs(["🎯 Radar de Oportunidades", "💼 Carteira, Vendas & IR"])

# ======================================================================== ABA 1
with aba1:
    st.subheader("1️⃣ Selecione seu perfil de risco")
    perfil = st.radio("Perfil de investidor (obrigatório antes da busca)", an.PERFIS, index=None,
                      horizontal=True, key="perfil")
    buscar = st.button("🔍 Buscar oportunidades do dia", type="primary", disabled=perfil is None)
    if perfil is None:
        st.info("Escolha **Conservador / Seguro** ou **Arrojado / Maior Risco** para liberar a busca.")
    if buscar:
        st.session_state["busca_perfil"] = perfil
    if perfil and st.session_state.get("busca_perfil") == perfil:
        with st.spinner("Analisando o universo de ativos da B3..."):
            recs = recomendacoes_do_dia(perfil, DIA)
        aviso_dados(all(r["live"] for r in recs.values()))
        st.subheader(f"2️⃣ Top 1 do dia por categoria — perfil {perfil}")
        for cat, rec in recs.items():
            t, m = rec["ticker"], rec["metricas"]
            tipo = dfc.TIPO_POR_CATEGORIA[cat]
            with st.container(border=True):
                c1, c2 = st.columns([3, 1])
                c1.markdown(f"### {an.CATEGORIAS[cat]}: **{t}**")
                c2.metric("Score do dia", f"{rec['ranking'].loc[t, 'score']:.0f}/100")
                k = st.columns(5)
                k[0].metric("Cotação", tc.brl(m["preco"]), f"{m['ret_1m']:.1%} (1m)")
                k[1].metric("Retorno 12m", f"{m['ret_12m']:.1%}")
                k[2].metric("Dividend Yield 12m", f"{m['dy']:.2%}")
                k[3].metric("Volatilidade", f"{m['vol']:.1%}")
                k[4].metric("Tendência", m["tendencia"])
                h, _ = hist_cache(t, DIA)
                st.plotly_chart(grafico(h, f"{t} — 12 meses com médias móveis"), width="stretch",
                                key=f"g_rec_{cat}")
                with st.expander("🔭 Análise de longo prazo"):
                    for linha in an.analise_longo_prazo(cat, m, fund_cache(t, DIA)):
                        st.markdown(f"- {linha}")
                with st.expander("🏁 Ranking completo da categoria"):
                    rk = rec["ranking"][["score", "preco", "ret_3m", "ret_12m", "dy", "vol", "tendencia"]].copy()
                    st.dataframe(rk.style.format({"score": "{:.0f}", "preco": "R$ {:.2f}", "ret_3m": "{:.1%}",
                                                  "ret_12m": "{:.1%}", "dy": "{:.2%}", "vol": "{:.1%}"}),
                                 width="stretch")

                fkey = f"form_compra_{cat}"
                if st.button("✅ Comprei este ativo hoje", key=f"btn_{fkey}"):
                    st.session_state[fkey] = not st.session_state.get(fkey, False)
                if st.session_state.get(fkey):
                    with st.form(f"f_{fkey}", border=True):
                        st.markdown(f"**Registrar compra de {t}**")
                        fc = st.columns(3)
                        q = fc[0].number_input("Quantidade", min_value=1, value=100, step=1, key=f"q_{fkey}")
                        p = fc[1].number_input("Preço pago (R$)", min_value=0.01, value=round(m["preco"], 2),
                                               step=0.01, format="%.2f", key=f"p_{fkey}")
                        d = fc[2].date_input("Data da compra", value=HOJE, max_value=HOJE, key=f"d_{fkey}",
                                             format="DD/MM/YYYY")
                        if st.form_submit_button("💾 Salvar na carteira", type="primary"):
                            db.registrar_compra(t, tipo, d, int(q), float(p), "Recomendação do Dia")
                            st.session_state[fkey] = False
                            st.session_state["flash"] = f"Compra de {int(q)} × {t} a {tc.brl(p)} registrada!"
                            st.rerun()

    if msg := st.session_state.pop("flash", None):
        st.success(msg, icon="✅")

    with st.expander("➕ Registrar compra manual (ativo fora da recomendação)"):
        with st.form("f_compra_manual"):
            mc = st.columns(5)
            mt = mc[0].text_input("Ticker (ex.: PETR4, HGLG11)").upper().strip()
            mtipo = mc[1].selectbox("Tipo", db.TIPOS)
            mq = mc[2].number_input("Quantidade", min_value=1, value=100, step=1)
            mp = mc[3].number_input("Preço pago (R$)", min_value=0.01, value=10.00, step=0.01, format="%.2f")
            md = mc[4].date_input("Data", value=HOJE, max_value=HOJE, format="DD/MM/YYYY")
            if st.form_submit_button("💾 Salvar compra manual"):
                if not mt:
                    st.error("Informe o ticker.")
                else:
                    db.registrar_compra(mt, mtipo, md, int(mq), float(mp), "Manual")
                    st.session_state["flash"] = f"Compra manual de {int(mq)} × {mt} registrada!"
                    st.rerun()

# ======================================================================== ABA 2
with aba2:
    vendas = db.listar_vendas()
    _, apuracoes = tc.apurar(vendas)
    ap = tc.apuracao_do_mes(apuracoes, MES_ATUAL)
    perfil_carteira = st.session_state.get("perfil") or an.PERFIS[0]

    if msg := st.session_state.pop("flash2", None):
        st.success(msg, icon="💸")

    # ---------------- A. Termômetro mensal de isenção
    st.subheader(f"🌡️ Termômetro de Isenção de IR — {HOJE.strftime('%m/%Y')}")
    vendido = ap.vendas_acoes
    saldo = tc.margem_isenta(vendido)
    with st.container(border=True):
        t1, t2, t3 = st.columns([1, 1, 1.6])
        t1.metric("Total vendido em ações no mês", tc.brl(vendido))
        t2.metric("Saldo restante dentro da isenção", tc.brl(saldo))
        if ap.isento_acoes:
            t3.success("**Status tributário atual:** 🟢 DENTRO DA ISENÇÃO")
        else:
            t3.error("**Status tributário atual:** 🔴 TETO DE R$ 20 MIL ULTRAPASSADO - TRIBUTÁVEL EM 15%")
        st.progress(min(vendido / tc.LIMITE_ISENCAO_ACOES, 1.0),
                    text=f"{vendido / tc.LIMITE_ISENCAO_ACOES:.1%} do teto de R$ 20.000,00 utilizado "
                         "(somente vendas de AÇÕES; FIIs não entram no teto)")

        st.markdown("##### 🧾 Resumo de IR devido no mês (DARF código 6015)")
        r = st.columns(5)
        r[0].metric("IR Ações (15%, acima de R$ 20 mil)", tc.brl(ap.ir_acoes))
        r[1].metric("IR FIIs (20%)", tc.brl(ap.ir_fii))
        r[2].metric("DARF a pagar", tc.brl(ap.darf_a_pagar),
                    help=f"Vencimento: {ap.vencimento_darf:%d/%m/%Y} (último dia útil do mês seguinte). "
                         "Valores < R$ 10,00 acumulam para o mês seguinte.")
        r[3].metric("Prejuízo a compensar — Ações", tc.brl(ap.prejuizo_acumulado_acoes))
        r[4].metric("Prejuízo a compensar — FIIs", tc.brl(ap.prejuizo_acumulado_fii))
        if ap.ir_residual_proximo > 0:
            st.caption(f"IR de {tc.brl(ap.ir_residual_proximo)} abaixo do mínimo de R$ 10,00 — "
                       "será somado ao DARF do próximo mês.")
        if ap.vendas_fii:
            st.caption(f"Vendas de FIIs no mês: {tc.brl(ap.vendas_fii)} · resultado {tc.brl(ap.resultado_fii)}")

        with st.expander("➕ Adicionar venda externa (feita fora do painel neste mês)"):
            with st.form("f_venda_externa"):
                ec = st.columns(6)
                et = ec[0].text_input("Ticker").upper().strip()
                etipo = ec[1].selectbox("Tipo", db.TIPOS, key="ext_tipo")
                eq = ec[2].number_input("Quantidade", min_value=1, value=100, step=1, key="ext_q")
                epm = ec[3].number_input("Preço médio de compra (R$)", min_value=0.0, value=10.0, step=0.01,
                                         format="%.2f", key="ext_pm")
                epv = ec[4].number_input("Preço de venda (R$)", min_value=0.01, value=10.0, step=0.01,
                                         format="%.2f", key="ext_pv")
                ed = ec[5].date_input("Data da venda", value=HOJE, max_value=HOJE, format="DD/MM/YYYY",
                                      key="ext_d")
                if st.form_submit_button("💾 Somar venda externa ao controle"):
                    if not et:
                        st.error("Informe o ticker.")
                    else:
                        db.registrar_venda_externa(et, etipo, ed, int(eq), float(epm), float(epv))
                        st.session_state["flash2"] = f"Venda externa de {et} adicionada ao controle mensal."
                        st.rerun()

    # ---------------- B/C. Posições ativas
    st.subheader("💼 Posições ativas — diagnóstico líquido e execução de venda")
    st.caption(f"Perfil usado nos vereditos: **{perfil_carteira}** (altere na aba Radar).")
    posicoes = db.listar_posicoes()
    if posicoes.empty:
        st.info("Nenhuma posição ativa. Registre uma compra na aba **🎯 Radar de Oportunidades**.")

    for pos in posicoes.to_dict("records"):
        pid, tk = pos["id"], pos["ticker"]
        try:
            h, live = hist_cache(tk, DIA)
            px = round(float(h["Close"].iloc[-1]), 2)
        except Exception as e:  # ticker inválido etc.
            st.error(f"Não foi possível obter cotação de {tk}: {e}")
            continue
        dg = an.diagnosticar(pos, px, h, vendas, perfil_carteira, DIA)
        icone, estilo = VEREDITO_UI[dg.veredito]
        with st.container(border=True):
            hc = st.columns([3, 2])
            hc[0].markdown(f"### {tk} · {'Ação' if dg.tipo_ativo == 'ACAO' else 'FII'} · "
                           f"{dg.quantidade} cotas @ {tc.brl(dg.preco_medio)}")
            hc[0].caption(f"Comprado em {pd.Timestamp(pos['data_compra']):%d/%m/%Y} · Origem: {pos['origem']}"
                          + ("" if live else " · ⚠️ cotação SIMULADA"))
            getattr(hc[1], estilo)(f"**Veredito:** {icone} {dg.veredito}")

            k = st.columns(5)
            k[0].metric("Valor investido", tc.brl(dg.valor_investido))
            k[1].metric("Valor hoje", tc.brl(dg.valor_hoje), f"cotação {tc.brl(px)}", delta_color="off")
            k[2].metric("Lucro bruto", tc.brl(dg.lucro_bruto), f"{dg.retorno_pct:+.2%}")
            k[3].metric("Imposto estimado se vender tudo hoje", tc.brl(dg.ir_vender_tudo),
                        help="Impacto incremental no IR do mês, incluindo o efeito retroativo sobre "
                             "vendas anteriores caso o teto de R$ 20 mil seja estourado.")
            k[4].metric("Lucro líquido real no bolso", tc.brl(dg.lucro_liquido))

            if dg.tipo_ativo == tc.ACAO:
                if dg.qtd_max_isenta >= dg.quantidade:
                    st.info(f"🟢 Margem isenta disponível no mês: **{tc.brl(dg.margem_isenta)}** — dá para "
                            f"vender **toda a posição** ({tc.brl(dg.valor_hoje)}) sem IR.")
                elif dg.qtd_max_isenta > 0:
                    st.info(f"🟡 Margem isenta disponível no mês: **{tc.brl(dg.margem_isenta)}** → **Venda "
                            f"Parcial Isenta** de até **{dg.qtd_max_isenta} cotas** "
                            f"({tc.brl(dg.valor_venda_isenta)}, lucro de {tc.brl(dg.lucro_venda_isenta)} sem IR).")
                else:
                    st.info(f"🔴 Margem isenta disponível no mês: **{tc.brl(dg.margem_isenta)}** — qualquer "
                            "venda de ações hoje é tributável (15% sobre o ganho líquido do mês).")
            else:
                st.info("ℹ️ FIIs não têm faixa de isenção: ganho de capital tributado a 20% "
                        "(e a venda não consome o teto de R$ 20 mil das ações).")
            with st.expander("🧠 Motivos do veredito"):
                for mtv in dg.motivos:
                    st.markdown(f"- {mtv}")
            with st.expander("📈 Gráfico desde a compra"):
                st.plotly_chart(grafico(h, f"{tk} desde a compra", dg.preco_medio, pos["data_compra"], 380),
                                width="stretch", key=f"g_pos_{pid}")

            # ---------- C. Fluxo de venda
            vkey = f"venda_aberta_{pid}"
            b1, b2 = st.columns([1, 5])
            if b1.button("💸 Vender este Ativo", key=f"btn_vender_{pid}", type="primary"):
                st.session_state[vkey] = not st.session_state.get(vkey, False)
                st.session_state[f"v_qtd_{pid}"] = dg.quantidade
                st.session_state[f"v_preco_{pid}"] = round(px, 2)
                st.session_state[f"v_data_{pid}"] = HOJE
            if st.session_state.get(vkey):
                with st.container(border=True):
                    st.markdown(f"#### Confirmar venda de **{tk}**")
                    st.session_state.setdefault(f"v_qtd_{pid}", dg.quantidade)
                    st.session_state.setdefault(f"v_preco_{pid}", round(px, 2))
                    st.session_state.setdefault(f"v_data_{pid}", HOJE)
                    fc = st.columns([1, 1, 1, 1.3])
                    q = fc[0].number_input("Quantidade a vender", min_value=1, max_value=dg.quantidade, step=1,
                                           key=f"v_qtd_{pid}")
                    pv = fc[1].number_input("Preço unitário de venda (R$)", min_value=0.01, step=0.01,
                                            format="%.2f", key=f"v_preco_{pid}")
                    dv = fc[2].date_input("Data da venda", min_value=pd.Timestamp(pos["data_compra"]).date(),
                                          max_value=HOJE, format="DD/MM/YYYY", key=f"v_data_{pid}")
                    if dg.tipo_ativo == tc.ACAO:
                        mes_v = tc.mes_referencia(dv)
                        ap_v = tc.apuracao_do_mes(apuracoes, mes_v)
                        q_iso = tc.qtd_maxima_isenta(ap_v.vendas_acoes, float(pv), dg.quantidade)

                        def _preencher(k=f"v_qtd_{pid}", v=q_iso):
                            st.session_state[k] = v
                        fc[3].write("")
                        fc[3].button(f"Preencher qtd. máxima isenta ({q_iso})", key=f"btn_iso_{pid}",
                                     on_click=_preencher, disabled=q_iso < 1)

                    sim = tc.simular_venda(vendas, dg.tipo_ativo, dv, int(q), float(pv), dg.preco_medio)
                    parcial = int(q) < dg.quantidade
                    st.markdown(f"**Simulação pré-confirmação** · Venda **{'PARCIAL' if parcial else 'TOTAL'}** "
                                f"de {int(q)} cotas = **{tc.brl(sim.valor_venda)}** · resultado da operação "
                                f"{tc.brl(sim.lucro_venda)}"
                                + (f" · restarão {dg.quantidade - int(q)} cotas a {tc.brl(dg.preco_medio)}"
                                   if parcial else ""))
                    if sim.ir_incremental > 0:
                        st.warning("⚠️ " + sim.mensagem)
                    else:
                        st.success("✅ " + sim.mensagem)
                    s1, s2 = st.columns(2)
                    s1.metric("IR desta operação (incremental)", tc.brl(max(sim.ir_incremental, 0)))
                    s2.metric("Lucro líquido da operação", tc.brl(sim.lucro_venda - max(sim.ir_incremental, 0)))

                    cc = st.columns([1, 1, 4])
                    if cc[0].button("✅ Confirmar e Registrar Venda", key=f"btn_conf_{pid}", type="primary"):
                        try:
                            db.registrar_venda(pid, int(q), float(pv), dv)
                        except ValueError as e:
                            st.error(str(e))
                        else:
                            for kk in (vkey, f"v_qtd_{pid}", f"v_preco_{pid}", f"v_data_{pid}"):
                                st.session_state.pop(kk, None)
                            st.session_state["flash2"] = (
                                f"Venda {'parcial' if parcial else 'total'} de {int(q)} × {tk} registrada "
                                f"({tc.brl(sim.valor_venda)}). Termômetro e IR do mês recalculados.")
                            st.rerun()
                    if cc[1].button("Cancelar", key=f"btn_cancel_{pid}"):
                        st.session_state[vkey] = False
                        st.rerun()

            with b2.popover("⚙️"):
                st.caption("Excluir posição lançada por engano (não gera venda).")
                if st.button("🗑️ Excluir posição", key=f"btn_del_{pid}"):
                    db.excluir_posicao(pid)
                    st.rerun()

    # ---------------- D. Histórico & DARF
    st.subheader("📜 Histórico de Vendas & Controle de DARF")
    with st.expander("Abrir histórico e apuração mensal", expanded=not vendas.empty):
        if vendas.empty:
            st.info("Nenhuma venda registrada ainda.")
        else:
            meses = sorted(vendas["mes_referencia"].unique(), reverse=True)
            filtro = st.selectbox("Filtrar por mês (YYYY-MM)", ["Todos"] + meses, key="filtro_mes")
            vf = vendas if filtro == "Todos" else vendas[vendas["mes_referencia"] == filtro]
            tabela = vf[["id", "data_venda", "mes_referencia", "ticker", "tipo_ativo", "quantidade_vendida",
                         "preco_medio_compra", "preco_venda", "valor_total_venda", "lucro_prejuizo_bruto",
                         "isento", "ir_devido", "origem_venda"]].rename(columns={
                "data_venda": "Data", "mes_referencia": "Mês", "ticker": "Ticker", "tipo_ativo": "Tipo",
                "quantidade_vendida": "Qtd", "preco_medio_compra": "PM compra", "preco_venda": "Preço venda",
                "valor_total_venda": "Valor venda", "lucro_prejuizo_bruto": "Lucro/Prejuízo",
                "isento": "Isento", "ir_devido": "IR estimado", "origem_venda": "Origem"})
            tabela["Isento"] = tabela["Isento"].map({True: "🟢 Isento", False: "🔴 Tributável"})
            st.dataframe(tabela.style.format({"PM compra": "R$ {:.2f}", "Preço venda": "R$ {:.2f}",
                                              "Valor venda": "R$ {:,.2f}", "Lucro/Prejuízo": "R$ {:,.2f}",
                                              "IR estimado": "R$ {:,.2f}"}),
                         hide_index=True, width="stretch")

            st.markdown("##### Apuração mensal")
            linhas = [{"Mês": a.mes, "Vendas ações": a.vendas_acoes, "Resultado ações": a.resultado_acoes,
                       "Situação ações": "🟢 Isento" if a.isento_acoes else "🔴 Tributável",
                       "Prej. compensado ações": a.prejuizo_compensado_acoes, "IR ações": a.ir_acoes,
                       "Vendas FII": a.vendas_fii, "Resultado FII": a.resultado_fii, "IR FII": a.ir_fii,
                       "DARF a pagar": a.darf_a_pagar, "Vencimento": a.vencimento_darf.strftime("%d/%m/%Y"),
                       "Prej. acum. ações": a.prejuizo_acumulado_acoes,
                       "Prej. acum. FII": a.prejuizo_acumulado_fii}
                      for a in apuracoes if filtro in ("Todos", a.mes)]
            moeda = {c: "R$ {:,.2f}" for c in linhas[0] if c not in ("Mês", "Situação ações", "Vencimento")}
            st.dataframe(pd.DataFrame(linhas).style.format(moeda), hide_index=True, width="stretch")

            st.markdown("##### ↩️ Estornar / excluir lançamento")
            opcoes = {f"#{r.id} · {r.data_venda} · {r.ticker} · {r.quantidade_vendida} un · "
                      f"{tc.brl(r.valor_total_venda)} · {r.origem_venda}": int(r.id) for r in vf.itertuples()}
            escolha = st.selectbox("Lançamento", list(opcoes), key="estorno_sel")
            conf = st.checkbox("Confirmo o estorno (vendas do painel devolvem a quantidade à carteira)",
                               key="estorno_conf")
            if st.button("↩️ Estornar lançamento", disabled=not conf, key="btn_estorno"):
                v = db.estornar_venda(opcoes[escolha])
                st.session_state.pop("estorno_conf", None)
                st.session_state["flash2"] = (f"Lançamento #{v['id']} ({v['ticker']}) estornado"
                                              + (" e quantidade devolvida à carteira." if v["origem_venda"] == "Painel"
                                                 else "."))
                st.rerun()

# ======================================================================== rodapé
st.divider()
st.caption(
    "⚖️ **Aviso legal:** os cálculos tributários seguem as regras vigentes da Receita Federal para operações "
    "comuns (Swing Trade) no mercado à vista da B3 — isenção para alienações de ações até R$ 20.000,00/mês, "
    "alíquota de 15% sobre o ganho líquido de ações acima do teto e 20% para FIIs, compensação de prejuízos "
    "por categoria e DARF mínimo de R$ 10,00. Não contemplam day trade, IRRF \"dedo-duro\" (0,005%), "
    "custos de corretagem/emolumentos, units, ETFs, BDRs ou eventos corporativos. Servem como **apoio "
    "gerencial** e **não substituem a apuração oficial** nem a orientação de um contador. As recomendações "
    "são geradas por modelo quantitativo e não constituem recomendação de investimento.")
