import streamlit as st
import yfinance as yf
import pandas as pd

st.set_page_config(page_title="Máquina de Renda", layout="centered")
st.title("💸 Máquina de Patrimônio e Salário Mensal")

aporte = st.number_input("Valor do Aporte Mensal (R$):", min_value=100.0, value=700.0, step=50.0)
renda_fixa = aporte * 0.70
acoes_orcamento = aporte * 0.15
fiis_orcamento = aporte * 0.15

tickers_acoes = ['BBAS3.SA', 'TAEE11.SA', 'EGIE3.SA', 'VALE3.SA']
tickers_fiis = ['MXRF11.SA', 'CPTS11.SA', 'BTLG11.SA']

if st.button("Analisar Mercado Agora"):
    dados_tabela = []
    
    with st.spinner("Conectando à B3 e validando fundamentos..."):
        
        # 1. Analisando Ações
        for ticker in tickers_acoes:
            try:
                acao = yf.Ticker(ticker)
                info = acao.info
                
                pl = info.get('trailingPE')
                dy = info.get('dividendYield', 0) or 0
                
                # Correção do Bug do Yahoo Finance: só multiplica se for decimal
                dy = (dy * 100) if dy < 1 else dy
                preco = info.get('currentPrice', info.get('previousClose', 0))
                
                # Tratamento contra falha no servidor (P/L vazio)
                if pl is None:
                    dados_tabela.append({"Classe": "📈 Ação", "Ativo": ticker.replace('.SA', ''), "Preço (R$)": round(preco, 2), "Ação": "🟡 MANTER", "Aporte": "-", "Motivo": "Falha na B3: Sem dados de P/L hoje."})
                elif 0 < pl <= 15 and dy >= 6:
                    dados_tabela.append({"Classe": "📈 Ação", "Ativo": ticker.replace('.SA', ''), "Preço (R$)": round(preco, 2), "Ação": "🟢 COMPRAR", "Aporte": 0, "Motivo": f"Barata (P/L: {pl:.1f}) e Bom DY"})
                elif pl > 30 or pl < 0:
                    dados_tabela.append({"Classe": "📈 Ação", "Ativo": ticker.replace('.SA', ''), "Preço (R$)": round(preco, 2), "Ação": "🔴 VENDER", "Aporte": "-", "Motivo": "Ficou muito cara ou deu prejuízo."})
                else:
                    dados_tabela.append({"Classe": "📈 Ação", "Ativo": ticker.replace('.SA', ''), "Preço (R$)": round(preco, 2), "Ação": "🟡 MANTER", "Aporte": "-", "Motivo": "Sem desconto atrativo hoje."})
            except: pass

        # 2. Analisando FIIs
        for ticker in tickers_fiis:
            try:
                fundo = yf.Ticker(ticker)
                info = fundo.info
                
                dy = info.get('dividendYield', 0) or 0
                dy = (dy * 100) if dy < 1 else dy
                preco = info.get('currentPrice', info.get('previousClose', 0))
                
                if dy >= 8:
                    dados_tabela.append({"Classe": "🏢 FII (Salário)", "Ativo": ticker.replace('.SA', ''), "Preço (R$)": round(preco, 2), "Ação": "🟢 COMPRAR", "Aporte": 0, "Motivo": f"Paga aluguel todo mês (DY: {dy:.1f}%)"})
                else:
                    dados_tabela.append({"Classe": "🏢 FII (Salário)", "Ativo": ticker.replace('.SA', ''), "Preço (R$)": round(preco, 2), "Ação": "🟡 MANTER", "Aporte": "-", "Motivo": "Rendimento abaixo do ideal."})
            except: pass
            
        # 3. Matemática de Alocação Exata (Sanity Check)
        acoes_comprar = [d for d in dados_tabela if d["Classe"] == "📈 Ação" and d["Ação"] == "🟢 COMPRAR"]
        fiis_comprar = [d for d in dados_tabela if d["Classe"] == "🏢 FII (Salário)" and d["Ação"] == "🟢 COMPRAR"]
        
        if len(acoes_comprar) > 0:
            for d in acoes_comprar:
                d["Aporte"] = f"R$ {acoes_orcamento / len(acoes_comprar):.2f}"
                
        if len(fiis_comprar) > 0:
            for d in fiis_comprar:
                d["Aporte"] = f"R$ {fiis_orcamento / len(fiis_comprar):.2f}"

        # 4. Renda Fixa (Soma exata dos 70%)
        dados_tabela.append({"Classe": "🛡️ Renda Fixa", "Ativo": "Tesouro Selic", "Preço (R$)": "-", "Ação": "🟢 COMPRAR", "Aporte": f"R$ {renda_fixa:.2f}", "Motivo": "Caixa de segurança (rende todo dia)."})
        
        df = pd.DataFrame(dados_tabela).sort_values(by=["Ação", "Classe"])
        st.dataframe(df, use_container_width=True)
