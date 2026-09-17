"""
Máquina de Patrimônio e Salário Mensal — versão corrigida.

Fontes de dados
---------------
  brapi.dev  -> preço (token gratuito; delay ~30 min no plano free)
  yfinance   -> LPA, P/VP e histórico de proventos (scraper não oficial, sujeito a 429)

ATENÇÃO: nenhuma das duas é tempo real. A B3 só libera cotação real-time
em feed pago. Trate os preços como atrasados em 15 a 30 minutos.

Instalação:
    pip install streamlit yfinance pandas requests
Execução:
    streamlit run maquina_de_renda.py
"""

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
FUSO_BR = timezone(timedelta(hours=-3))
BRAPI_URL = "https://brapi.dev/api/quote/{ticker}"
TTL_CACHE = 900  # 15 minutos

COMPRAR = "🟢 COMPRAR"
MANTER = "🟡 MANTER"
REVISAR = "🔴 REVISAR"
SEM_DADOS = "⚠️ SEM DADOS"

# Ordem explícita. Ordenar pelo texto ordenaria pelo codepoint do emoji,
# e 🔴 (U+1F534) < 🟡 (U+1F7E1) < 🟢 (U+1F7E2) — ou seja, o inverso do útil.
ORDEM_STATUS = {COMPRAR: 0, MANTER: 1, REVISAR: 2, SEM_DADOS: 3}

ACOES_PADRAO = "BBAS3, TAEE11, EGIE3, VALE3"
FIIS_PADRAO = "MXRF11, CPTS11, BTLG11"

st.set_page_config(page_title="Máquina de Renda", layout="wide")


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------
def centavos(valor: float) -> float:
    """Arredonda pra baixo em centavos. Nunca gera aporte maior que o orçamento."""
    return float(Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def num(*valores):
    """
    Primeiro valor que seja número de verdade.
    Descarta None, NaN, string e zero — que é como o Yahoo devolve campo ausente.
    """
    for v in valores:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f == f and f != 0:  # f == f descarta NaN
            return f
    return None


def hora_curta(iso) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.astimezone(FUSO_BR).strftime("%d/%m %H:%M")
    except (ValueError, TypeError):
        return "—"


def parse_tickers(texto: str) -> list[str]:
    limpos = []
    for parte in texto.replace("\n", ",").replace(";", ",").split(","):
        t = parte.strip().upper().replace(".SA", "")
        if t and t not in limpos:
            limpos.append(t)
    return limpos


def token_padrao() -> str:
    try:
        return st.secrets.get("BRAPI_TOKEN", "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Coleta de dados (tudo cacheado — sem cache, cada clique refaz todas as chamadas)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=TTL_CACHE, show_spinner=False)
def preco_brapi(ticker: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.get(BRAPI_URL.format(ticker=ticker), headers=headers, timeout=10)
    except requests.RequestException as erro:
        return {"erro": f"brapi inacessível ({type(erro).__name__})"}

    if resp.status_code in (401, 403):
        return {"erro": "token da brapi inválido ou sem permissão"}
    if resp.status_code == 429:
        return {"erro": "cota mensal da brapi esgotada"}
    if resp.status_code != 200:
        return {"erro": f"brapi devolveu HTTP {resp.status_code}"}

    try:
        resultado = (resp.json().get("results") or [{}])[0]
    except ValueError:
        return {"erro": "brapi devolveu resposta inválida"}

    preco = num(resultado.get("regularMarketPrice"))
    if preco is None:
        return {"erro": "brapi não trouxe preço para este ticker"}
    return {"preco": preco, "atualizado": resultado.get("regularMarketTime")}


@st.cache_data(ttl=TTL_CACHE, show_spinner=False)
def dados_yahoo(ticker_sa: str) -> dict:
    try:
        papel = yf.Ticker(ticker_sa)
        info = papel.info or {}
    except Exception as erro:
        # Nunca use `except: pass` aqui. O ativo sumiria da tabela em silêncio.
        return {"erro": f"yfinance falhou ({type(erro).__name__})"}

    # .get('chave', default) NÃO cai no default quando a chave existe valendo None,
    # que é o caso comum de currentPrice em ticker da B3. Por isso o encadeamento com `or`.
    preco = num(
        info.get("currentPrice"),
        info.get("regularMarketPrice"),
        info.get("previousClose"),
    )

    return {
        "preco": preco,
        "pl_bruto": num(info.get("trailingPE")),
        "lpa": num(info.get("trailingEps")),
        "pvp": num(info.get("priceToBook")),
        "proventos_12m": proventos_12m(papel),
    }


def proventos_12m(papel) -> float | None:
    """
    Soma os proventos pagos nos últimos 365 dias.
    O campo `dividendYield` do Yahoo é o problema original: vem vazio para a
    maioria dos FIIs brasileiros e sem unidade consistente para as ações
    (ora decimal, ora percentual). Calcular do histórico elimina os dois casos.
    """
    try:
        divs = papel.dividends
    except Exception:
        return None
    if divs is None or divs.empty:
        return None

    agora = pd.Timestamp.now(tz=divs.index.tz) if divs.index.tz is not None else pd.Timestamp.now()
    return float(divs[divs.index >= agora - pd.Timedelta(days=365)].sum())


def coletar(ticker: str, classe: str, token: str) -> dict:
    brapi = preco_brapi(ticker, token)
    yahoo = dados_yahoo(f"{ticker}.SA")

    preco = num(brapi.get("preco"), yahoo.get("preco"))
    fonte = "brapi" if brapi.get("preco") else ("yahoo" if yahoo.get("preco") else "—")

    # Recalcula P/L com o preço efetivamente usado; trailingPE é fallback.
    lpa = yahoo.get("lpa")
    pl = (preco / lpa) if (lpa and preco) else yahoo.get("pl_bruto")

    proventos = yahoo.get("proventos_12m")
    dy = (proventos / preco * 100) if (proventos is not None and preco) else None

    avisos = [m for m in (brapi.get("erro"), yahoo.get("erro")) if m]

    return {
        "ticker": ticker,
        "classe": classe,
        "preco": preco,
        "pl": pl,
        "pvp": yahoo.get("pvp"),
        "dy": dy,
        "fonte": fonte,
        "atualizado": hora_curta(brapi.get("atualizado")),
        "avisos": " | ".join(avisos),
        "aporte": None,
        "cotas": None,
    }


# ---------------------------------------------------------------------------
# Decisão
# ---------------------------------------------------------------------------
def avaliar(ativo: dict, cfg: dict) -> tuple[str, str]:
    if ativo["preco"] is None:
        return SEM_DADOS, ativo["avisos"] or "nenhuma fonte devolveu preço"

    dy, pl, pvp = ativo["dy"], ativo["pl"], ativo["pvp"]

    if ativo["classe"] == "Ação":
        faltando = [nome for nome, valor in (("P/L", pl), ("DY", dy)) if valor is None]
        if faltando:
            return SEM_DADOS, f"sem {' e '.join(faltando)} nas fontes hoje"
        if pl < 0:
            return REVISAR, "prejuízo nos últimos 12 meses"
        if pl > cfg["pl_alto"]:
            return REVISAR, f"P/L {pl:.1f} acima do teto de revisão"
        if pl <= cfg["pl_max"] and dy >= cfg["dy_acao"]:
            return COMPRAR, f"P/L {pl:.1f} e DY {dy:.1f}% dentro do critério"
        return MANTER, f"P/L {pl:.1f} / DY {dy:.1f}% fora do critério de compra"

    # FII
    if dy is None:
        return SEM_DADOS, "sem histórico de rendimentos nas fontes"
    if pvp is not None and pvp > cfg["pvp_max"]:
        return MANTER, f"P/VP {pvp:.2f} acima do teto de {cfg['pvp_max']:.2f}"
    if dy < cfg["dy_fii"]:
        return MANTER, f"DY {dy:.1f}% abaixo do mínimo de {cfg['dy_fii']:.1f}%"
    complemento = f" e P/VP {pvp:.2f}" if pvp is not None else " (P/VP indisponível)"
    return COMPRAR, f"DY {dy:.1f}%{complemento}"


# ---------------------------------------------------------------------------
# Alocação
# ---------------------------------------------------------------------------
def alocar(ativos: list[dict], aporte: float, pesos: dict) -> tuple[float, float]:
    """
    Distribui o orçamento de cada classe entre os ativos marcados como COMPRAR.
    A renda fixa absorve o piso, as sobras de classes sem candidato e os centavos
    do arredondamento — garantindo que a soma bata com o aporte informado.
    """
    total_variavel = 0.0
    for classe, peso in (("Ação", pesos["acoes"]), ("FII", pesos["fiis"])):
        alvos = [a for a in ativos if a["classe"] == classe and a["status"] == COMPRAR]
        if not alvos:
            continue
        cota = centavos(aporte * peso / len(alvos))
        for ativo in alvos:
            ativo["aporte"] = cota
            ativo["cotas"] = int(cota // ativo["preco"]) if ativo["preco"] else 0
            total_variavel += cota

    renda_fixa = centavos(aporte - total_variavel)
    sobra = centavos(renda_fixa - aporte * pesos["rf"])
    return renda_fixa, sobra


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------
st.title("💸 Máquina de Patrimônio e Salário Mensal")
st.caption(
    "Preços atrasados em 15 a 30 minutos. Nenhuma fonte gratuita entrega cotação "
    "real-time da B3 — isso existe apenas em feed pago."
)

with st.sidebar:
    st.header("Aporte")
    aporte = st.number_input("Valor mensal (R$)", min_value=100.0, value=700.0, step=50.0)

    st.subheader("Divisão")
    p_rf = st.slider("Renda fixa (%)", 0, 100, 70)
    p_acoes = st.slider("Ações (%)", 0, 100, 15)
    p_fiis = st.slider("FIIs (%)", 0, 100, 15)
    soma_pesos = p_rf + p_acoes + p_fiis
    if soma_pesos != 100:
        st.error(f"Os pesos somam {soma_pesos}%. Ajuste para 100%.")

    st.subheader("Ativos")
    acoes_txt = st.text_area("Ações", ACOES_PADRAO, height=70)
    fiis_txt = st.text_area("FIIs", FIIS_PADRAO, height=70)

    st.subheader("Critérios")
    st.caption("São heurísticas suas, não verdades de mercado. Ajuste por setor.")
    pl_max = st.number_input("P/L máximo para comprar", value=15.0, step=1.0)
    pl_alto = st.number_input("P/L acima disto → revisar", value=30.0, step=1.0)
    dy_acao = st.number_input("DY mínimo — ações (%)", value=6.0, step=0.5)
    dy_fii = st.number_input("DY mínimo — FIIs (%)", value=8.0, step=0.5)
    pvp_max = st.number_input("P/VP máximo — FIIs", value=1.05, step=0.05)

    st.subheader("Fonte de dados")
    token = st.text_input(
        "Token brapi.dev",
        value=token_padrao(),
        type="password",
        help=(
            "Grátis em brapi.dev/dashboard (15.000 requisições/mês). "
            "Sem token o app cai só no Yahoo, que bloqueia por excesso de requisições — "
            "especialmente no Streamlit Cloud, onde o IP é compartilhado."
        ),
    )
    if not token:
        st.warning("Sem token da brapi: usando apenas o Yahoo. Espere falhas intermitentes.")

cfg = {
    "pl_max": pl_max,
    "pl_alto": pl_alto,
    "dy_acao": dy_acao,
    "dy_fii": dy_fii,
    "pvp_max": pvp_max,
}
pesos = {"rf": p_rf / 100, "acoes": p_acoes / 100, "fiis": p_fiis / 100}

if st.button("Analisar mercado agora", type="primary", disabled=soma_pesos != 100):
    lista = [(t, "Ação") for t in parse_tickers(acoes_txt)]
    lista += [(t, "FII") for t in parse_tickers(fiis_txt)]

    if not lista:
        st.warning("Informe pelo menos um ativo.")
        st.stop()

    barra = st.progress(0.0, text="Consultando fontes...")
    ativos = []
    for i, (ticker, classe) in enumerate(lista, start=1):
        barra.progress(i / len(lista), text=f"Consultando {ticker}...")
        ativos.append(coletar(ticker, classe, token))
        time.sleep(0.4)  # respiro entre chamadas ao Yahoo, reduz chance de 429
    barra.empty()

    for ativo in ativos:
        ativo["status"], ativo["motivo"] = avaliar(ativo, cfg)

    renda_fixa, sobra = alocar(ativos, aporte, pesos)

    motivo_rf = "Caixa de segurança, rende todo dia útil."
    if sobra > 0.01:
        motivo_rf += f" Inclui {brl(sobra)} que sobrou de classes sem candidato à compra."

    linhas = [
        {
            "Classe": "📈 Ação" if a["classe"] == "Ação" else "🏢 FII",
            "Ativo": a["ticker"],
            "Preço": a["preco"],
            "P/L": a["pl"],
            "P/VP": a["pvp"],
            "DY 12m (%)": a["dy"],
            "Decisão": a["status"],
            "Aporte": a["aporte"],
            "Cotas": a["cotas"],
            "Motivo": a["motivo"],
            "Fonte": a["fonte"],
            "Atualizado": a["atualizado"],
        }
        for a in ativos
    ]
    linhas.append(
        {
            "Classe": "🛡️ Renda fixa",
            "Ativo": "Tesouro Selic",
            "Preço": None,
            "P/L": None,
            "P/VP": None,
            "DY 12m (%)": None,
            "Decisão": COMPRAR,
            "Aporte": renda_fixa,
            "Cotas": None,
            "Motivo": motivo_rf,
            "Fonte": "—",
            "Atualizado": "—",
        }
    )

    df = pd.DataFrame(linhas).sort_values(
        by=["Decisão", "Classe"],
        key=lambda col: col.map(ORDEM_STATUS) if col.name == "Decisão" else col,
    )

    total = float(df["Aporte"].fillna(0).sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("Aporte informado", brl(aporte))
    c2.metric("Total alocado", brl(total))
    c3.metric("Diferença", brl(aporte - total))

    if abs(aporte - total) > 0.01:
        st.error("A soma não fechou com o aporte. Revise a alocação antes de executar.")
    else:
        st.success("Alocação confere com o aporte informado.")

    colunas = {
        "Preço": st.column_config.NumberColumn(format="R$ %.2f"),
        "Aporte": st.column_config.NumberColumn(format="R$ %.2f"),
        "P/L": st.column_config.NumberColumn(format="%.1f"),
        "P/VP": st.column_config.NumberColumn(format="%.2f"),
        "DY 12m (%)": st.column_config.NumberColumn(format="%.1f"),
        "Cotas": st.column_config.NumberColumn(format="%d"),
    }
    try:
        st.dataframe(df, hide_index=True, width="stretch", column_config=colunas)
    except TypeError:  # Streamlit anterior à 1.49
        st.dataframe(df, hide_index=True, use_container_width=True, column_config=colunas)

    sem_cota = [a["ticker"] for a in ativos if a["cotas"] == 0 and a["status"] == COMPRAR]
    if sem_cota:
        st.info(
            "Aporte insuficiente para 1 cota inteira de: "
            + ", ".join(sem_cota)
            + ". Considere concentrar o mês em menos ativos e rodar os demais no próximo."
        )

    avisos = [f"**{a['ticker']}** — {a['avisos']}" for a in ativos if a["avisos"]]
    if avisos:
        with st.expander(f"⚠️ {len(avisos)} aviso(s) de coleta"):
            for aviso in avisos:
                st.markdown(f"- {aviso}")

st.divider()
st.caption(
    "Ferramenta de apoio, não recomendação de investimento. Os cortes de P/L, DY e P/VP "
    "são parâmetros que você define e variam muito por setor. O app não conhece sua "
    "carteira: 🔴 REVISAR sinaliza que o indicador saiu da sua faixa, não que você deva vender."
)
