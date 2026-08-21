import streamlit as st
import yfinance as yf
import pandas as pd

# Configuração visual do App
st.set_page_config(page_title="Meu Gestor", layout="centered")
st.title("📊 Gestor Automático de Aportes")

# Input onde você digita o valor no app
aporte = st.number_input("Valor do Aporte Mensal (R$):", min_value=100.0, value=700.0, step=50.0)
acoes_orcamento = aporte * 0.30
renda_fixa = aporte * 0.70

tickers = ['BBAS3.SA', 'TAEE11.SA', 'EGIE3.SA', 'VALE3.SA', 'ITUB4.SA', 'WEGE3.SA']

# Botão de execução
if st.button("Analisar Mercado Agora"):
    dados_tabela = []
    
    # Barra de carregamento
    with st.spinner("Analisando tendências e balanços na B3..."):
        for ticker in tickers:
            try:
                acao = yf.Ticker(ticker)
                info = acao.info
                hist = acao.history(period="1y")
                
                if len(hist) < 200:
                    continue
                    
                pl = info.get('trailingPE', 0)
                dy = info.get('dividendYield', 0) * 100
                ativo = ticker.replace('.SA', '')
                
                media_200 = hist['Close'].tail(200).mean()
                preco_atual = hist['Close'].iloc[-1]
                tendencia_alta = preco_atual > media_200
                
                # Regras
                if 0 < pl < 15 and dy > 5 and tendencia_alta:
                    status = "🟢 COMPRAR"
                    valor = acoes_orcamento / 2
                    justif = f"Tendência Alta. DY: {dy:.1f}%"
                elif pl > 25 or (not tendencia_alta and preco_atual < media_200 * 0.9):
                    status = "🔴 VENDER"
                    valor = "Vender Tudo"
                    justif = "Perdeu tendência ou cara demais."
                else:
                    status = "🟡 MANTER"
                    valor = "-"
                    justif = "Sem sinal claro. Aguarde."
                    
                dados_tabela.append({
                    "Ação": status,
                    "Ativo": ativo,
                    "Preço (R$)": round(preco_atual, 2),
                    "Aporte (R$)": valor if isinstance(valor, str) else round(valor, 2),
                    "Justificativa": justif
                })
            except:
                continue
        
        # Renda Fixa
        dados_tabela.append({
            "Ação": "🟢 COMPRAR",
            "Ativo": "Tesouro Selic / IPCA+",
            "Preço (R$)": "-",
            "Aporte (R$)": round(renda_fixa, 2),
            "Justificativa": "Base de segurança da carteira."
        })
        
        # Mostra a tabela na tela do celular
        df = pd.DataFrame(dados_tabela).sort_values(by="Ação")
        st.dataframe(df, use_container_width=True)
