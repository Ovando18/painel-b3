"""Ciclo completo na UI: recomendar -> comprar -> Aba 2 -> venda parcial isenta ->
venda total (estoura o teto) -> verificar termômetro -> estornar."""
import math
import os
import sys

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)
os.environ.setdefault("PAINEL_OFFLINE", "1")  # determinístico e rápido nos testes

import analyzer as an  # noqa: E402
import database as db  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402


def _metric(at, label):
    return next(m.value for m in at.metric if m.label == label)


def _btn(at, prefixo):
    return next(b for b in at.button if b.label.startswith(prefixo))


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "ciclo.db"))
    at = AppTest.from_file(os.path.join(APP_DIR, "app.py"), default_timeout=180)
    at.run()
    assert not at.exception
    return at


def test_ciclo_completo(app):
    at = app
    # 1) Perfil obrigatório: busca desabilitada até escolher
    assert _btn(at, "🔍").disabled
    at.radio(key="perfil").set_value(an.PERFIS[0]).run()
    _btn(at, "🔍").click().run()
    assert not at.exception
    assert any("Ação Simples" in md.value for md in at.markdown)

    # 2) Registrar compra do Top 1 de Ação Simples (~R$ 30 mil)
    at.button(key="btn_form_compra_ACAO_SIMPLES").click().run()
    preco = at.number_input(key="p_form_compra_ACAO_SIMPLES").value
    qtd = math.ceil(30000 / preco)
    at.number_input(key="p_form_compra_ACAO_SIMPLES").set_value(round(preco * 0.8, 2))  # lucro de ~25%
    at.number_input(key="q_form_compra_ACAO_SIMPLES").set_value(qtd)
    _btn(at, "💾 Salvar na carteira").click().run()
    assert not at.exception
    pos = db.listar_posicoes()
    assert len(pos) == 1 and pos.iloc[0]["origem"] == "Recomendação do Dia"
    pid, tk = int(pos.iloc[0]["id"]), pos.iloc[0]["ticker"]
    assert _metric(at, "Total vendido em ações no mês") == "R$ 0,00"

    # 3) Aba 2: venda PARCIAL usando o atalho da quantidade máxima isenta
    at.button(key=f"btn_vender_{pid}").click().run()
    assert at.number_input(key=f"v_qtd_{pid}").value == qtd          # pré-preenchido com total
    at.button(key=f"btn_iso_{pid}").click().run()
    q_iso = at.number_input(key=f"v_qtd_{pid}").value
    assert 0 < q_iso < qtd
    assert any("100% ISENTO" in s.value for s in at.success)       # simulação pré-confirmação
    at.button(key=f"btn_conf_{pid}").click().run()
    assert not at.exception
    v = db.listar_vendas()
    assert len(v) == 1 and bool(v.iloc[0]["isento"]) and v.iloc[0]["ir_devido"] == 0
    assert db.obter_posicao(tk)["quantidade"] == qtd - q_iso
    vendido = float(v.iloc[0]["valor_total_venda"])
    assert vendido <= 20000
    assert any("DENTRO DA ISENÇÃO" in s.value for s in at.success)
    print(f"\n[parcial] {tk}: vendeu {q_iso}/{qtd} cotas = R$ {vendido:,.2f} · "
          f"termômetro: {_metric(at, 'Total vendido em ações no mês')} | "
          f"saldo isento: {_metric(at, 'Saldo restante dentro da isenção')}")

    # 4) Venda TOTAL do saldo -> ultrapassa R$ 20 mil -> recálculo retroativo
    at.button(key=f"btn_vender_{pid}").click().run()
    assert any("ULTRAPASSARÁ" in w.value for w in at.warning) or \
        db.obter_posicao(tk)["preco_pago"] >= at.number_input(key=f"v_preco_{pid}").value
    at.button(key=f"btn_conf_{pid}").click().run()
    assert not at.exception
    assert db.obter_posicao(tk) is None                              # posição removida
    v = db.listar_vendas()
    assert len(v) == 2 and not v["isento"].any()                      # ambas tributáveis agora
    assert (v["ir_devido"] > 0).all()                                 # IR retroativo na 1ª venda
    assert v["ir_devido"].sum() == pytest.approx(0.15 * v["lucro_prejuizo_bruto"].sum(), abs=0.02)
    assert any("TETO DE R$ 20 MIL ULTRAPASSADO" in e.value for e in at.error)
    print(f"[total] termômetro: {_metric(at, 'Total vendido em ações no mês')} | "
          f"IR ações: {_metric(at, 'IR Ações (15%, acima de R$ 20 mil)')} | "
          f"IR por venda: {v['ir_devido'].tolist()} | lucro: {v['lucro_prejuizo_bruto'].tolist()}")

    # 5) Estorno da venda total -> quantidade volta e mês volta a ser isento
    at.selectbox(key="estorno_sel").set_value(at.selectbox(key="estorno_sel").options[-1])
    at.checkbox(key="estorno_conf").check().run()
    at.button(key="btn_estorno").click().run()
    assert not at.exception
    assert db.obter_posicao(tk)["quantidade"] == qtd - q_iso
    assert db.listar_vendas()["isento"].all()
    assert any("DENTRO DA ISENÇÃO" in s.value for s in at.success)
