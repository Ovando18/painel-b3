import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
import tax_calculator as tc  # noqa: E402


def v(i, tipo, data, valor, lucro):
    return {"id": i, "tipo_ativo": tipo, "data_venda": data, "valor_total_venda": valor,
            "lucro_prejuizo_bruto": lucro}


# ------------------------------------------------------------ tax_calculator
def test_mes_dentro_da_isencao():
    df, ap = tc.apurar([v(1, "ACAO", "2026-11-03", 12000, 3000), v(2, "ACAO", "2026-11-10", 8000, 1000)])
    assert ap[0].vendas_acoes == 20000 and ap[0].isento_acoes  # exatamente R$ 20 mil é isento
    assert ap[0].ir_acoes == 0 and df["isento"].all()


def test_recalculo_retroativo_ao_estourar_teto():
    df, ap = tc.apurar([v(1, "ACAO", "2026-11-03", 12000, 3000), v(2, "ACAO", "2026-11-10", 9000, 1000)])
    assert not ap[0].isento_acoes
    assert ap[0].ir_acoes == pytest.approx(600.0)            # 15% de 4.000 (inclui a 1ª venda)
    assert df.set_index("id")["ir_devido"].to_dict() == {1: 450.0, 2: 150.0}
    assert not df["isento"].any()


def test_fii_nao_entra_no_teto_e_paga_20():
    _, ap = tc.apurar([v(1, "ACAO", "2026-11-03", 15000, 2000), v(2, "FII", "2026-11-04", 30000, 1000)])
    assert ap[0].vendas_acoes == 15000 and ap[0].isento_acoes
    assert ap[0].ir_fii == 200.0 and ap[0].ir_acoes == 0


def test_compensacao_de_prejuizo_e_mes_isento_nao_consome():
    _, ap = tc.apurar([
        v(1, "ACAO", "2026-08-05", 10000, -2000),   # prejuízo em mês isento acumula
        v(2, "ACAO", "2026-09-05", 5000, 1500),     # ganho isento: não consome prejuízo
        v(3, "ACAO", "2026-10-05", 25000, 5000),    # tributável: compensa 2000
    ])
    assert ap[0].prejuizo_acumulado_acoes == 2000
    assert ap[1].prejuizo_acumulado_acoes == 2000 and ap[1].ir_acoes == 0
    assert ap[2].prejuizo_compensado_acoes == 2000 and ap[2].ir_acoes == 450.0
    assert ap[2].prejuizo_acumulado_acoes == 0


def test_pools_separados_acao_fii():
    _, ap = tc.apurar([v(1, "FII", "2026-08-05", 5000, -1000), v(2, "ACAO", "2026-09-05", 30000, 2000)])
    assert ap[1].ir_acoes == 300.0 and ap[1].prejuizo_acumulado_fii == 1000


def test_darf_minimo_acumula():
    _, ap = tc.apurar([v(1, "FII", "2026-08-05", 1000, 30), v(2, "FII", "2026-09-05", 1000, 30)])
    assert ap[0].darf_a_pagar == 0 and ap[0].ir_residual_proximo == 6.0
    assert ap[1].darf_a_pagar == 12.0


def test_simulacao_e_qtd_maxima_isenta():
    hist = [v(1, "ACAO", "2026-11-03", 12000, 2000)]
    assert tc.qtd_maxima_isenta(12000, 25.0, 1000) == 320       # 8.000 / 25
    assert tc.qtd_maxima_isenta(12000, 25.0, 100) == 100
    s = tc.simular_venda(hist, "ACAO", "2026-11-14", 260, 25.0, 20.0)
    assert s.isento_depois and s.ir_incremental == 0 and "100% ISENTO" in s.mensagem
    s = tc.simular_venda(hist, "ACAO", "2026-11-14", 400, 25.0, 20.0)  # 10.000 -> total 22.000
    assert s.estoura_teto and s.ir_incremental == pytest.approx(0.15 * (2000 + 2000))
    assert "ULTRAPASSARÁ" in s.mensagem


def test_vencimento_darf():
    assert tc.ultimo_dia_util_mes_seguinte("2026-11").isoformat() == "2026-12-31"
    assert tc.ultimo_dia_util_mes_seguinte("2026-12").isoformat() == "2027-01-29"  # 31/01/27 é domingo


# ------------------------------------------------------------ database
@pytest.fixture()
def banco(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    return path


def test_ciclo_compra_venda_parcial_total_e_estorno(banco):
    pid = db.registrar_compra("PETR4", "ACAO", "2026-10-01", 1000, 20.0, "Recomendação do Dia")
    db.registrar_compra("PETR4", "ACAO", "2026-10-15", 1000, 30.0, "Manual")   # preço médio 25
    p = db.obter_posicao("PETR4")
    assert p["quantidade"] == 2000 and p["preco_pago"] == pytest.approx(25.0)

    db.registrar_venda(pid, 400, 30.0, "2026-11-03")                           # 12.000 isento
    assert db.obter_posicao("PETR4")["quantidade"] == 1600
    assert db.obter_posicao("PETR4")["preco_pago"] == pytest.approx(25.0)       # PM preservado
    vendas = db.listar_vendas("2026-11")
    assert vendas["isento"].all() and vendas["ir_devido"].sum() == 0

    db.registrar_venda(pid, 1600, 30.0, "2026-11-10")                          # total -> 60.000
    assert db.obter_posicao("PETR4") is None
    vendas = db.listar_vendas("2026-11")
    assert not vendas["isento"].any()
    assert vendas["ir_devido"].sum() == pytest.approx(0.15 * 2000 * 5)          # retroativo: 1.500

    db.estornar_venda(int(vendas.iloc[1]["id"]))                                # estorna a total
    p = db.obter_posicao("PETR4")
    assert p["quantidade"] == 1600 and p["preco_pago"] == pytest.approx(25.0)
    vendas = db.listar_vendas("2026-11")
    assert vendas["isento"].all() and vendas["ir_devido"].sum() == 0            # volta a ser isento


def test_venda_externa_soma_ao_teto(banco):
    pid = db.registrar_compra("VALE3", "ACAO", "2026-10-01", 100, 50.0, "Manual")
    db.registrar_venda_externa("ITUB4", "ACAO", "2026-11-02", 500, 30.0, 36.0)  # 18.000, lucro 3.000
    db.registrar_venda(pid, 100, 60.0, "2026-11-05")                           # +6.000 -> 24.000
    vendas = db.listar_vendas("2026-11")
    assert vendas["ir_devido"].sum() == pytest.approx(0.15 * (3000 + 1000))
    assert db.listar_posicoes().empty


def test_venda_maior_que_posicao_falha(banco):
    pid = db.registrar_compra("WEGE3", "ACAO", "2026-10-01", 10, 40.0, "Manual")
    with pytest.raises(ValueError):
        db.registrar_venda(pid, 11, 41.0, "2026-11-01")
    assert db.listar_vendas().empty
