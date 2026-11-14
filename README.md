# Painel B3 — Radar, Carteira & Controle de IR

```
app.py            # UI Streamlit (Aba 1: Radar + compra | Aba 2: carteira, venda, termômetro, DARF, histórico)
database.py       # SQLite (carteira.db): posicoes_ativas + historico_vendas, venda/estorno atômicos
data_fetcher.py   # yfinance (.SA) com fallback automático para dados simulados
analyzer.py       # indicadores, ranking diário por perfil, análise de longo prazo, veredito por posição
tax_calculator.py # apuração mensal (isenção R$ 20 mil, 15%/20%, compensação de prejuízo, DARF mínimo)
tests/            # unitários de IR/banco + ciclo completo na UI (Streamlit AppTest)
```

## Como rodar
```bash
pip install -r requirements.txt
streamlit run app.py
```
Variáveis opcionais: `CARTEIRA_DB=/caminho/carteira.db`, `PAINEL_OFFLINE=1` (força dados simulados).

## Testes
```bash
python -m pytest -q tests
```
