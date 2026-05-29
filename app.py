import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(page_title="Stock Comparison Dashboard", layout="wide")

st.title("Stock Comparison Dashboard")

col_input, col_range = st.columns([3, 1])

with col_input:
    tickers_input = st.text_input(
        "Enter ticker symbols (comma-separated)",
        value="COST, WMT",
        placeholder="e.g. AAPL, MSFT, GOOGL",
    )

with col_range:
    time_range = st.selectbox(
        "Time range",
        ["1M", "3M", "6M", "1Y", "2Y", "5Y", "10Y"],
        index=5,
    )

range_map = {
    "1M": 30, "3M": 90, "6M": 180,
    "1Y": 365, "2Y": 730, "5Y": 1825, "10Y": 3650,
}

tickers = [t.strip().upper() for t in tickers_input.split(",") if t.strip()]

if not tickers:
    st.warning("Enter at least one ticker symbol above.")
    st.stop()

end_date = datetime.today()
start_date = end_date - timedelta(days=range_map[time_range])


@st.cache_data(ttl=300)
def fetch_stock_data(ticker, start, end):
    stock = yf.Ticker(ticker)
    hist = stock.history(start=start, end=end)
    info = stock.info
    financials = stock.financials
    quarterly_financials = stock.quarterly_financials
    quarterly_income_stmt = stock.quarterly_income_stmt
    try:
        earnings_dates = stock.get_earnings_dates(limit=60)
    except Exception:
        earnings_dates = None
    balance_sheet = stock.balance_sheet
    return hist, info, financials, quarterly_financials, quarterly_income_stmt, earnings_dates, balance_sheet


with st.spinner("Fetching stock data..."):
    stock_data = {}
    failed = []
    for ticker in tickers:
        try:
            hist, info, financials, quarterly_financials, quarterly_income_stmt, earnings_dates, balance_sheet = fetch_stock_data(
                ticker, start_date, end_date
            )
            if hist.empty:
                failed.append(ticker)
                continue
            stock_data[ticker] = {
                "history": hist,
                "info": info,
                "financials": financials,
                "quarterly_financials": quarterly_financials,
                "quarterly_income_stmt": quarterly_income_stmt,
                "earnings_dates": earnings_dates,
                "balance_sheet": balance_sheet,
            }
        except Exception as e:
            failed.append(f"{ticker} ({e})")

if failed:
    st.error(f"Could not fetch data for: {', '.join(failed)}")

if not stock_data:
    st.warning("No valid stock data found. Check your ticker symbols.")
    st.stop()

# --- Key Metrics Table ---
st.header("Key Fundamentals")

metrics_rows = []
for ticker, data in stock_data.items():
    info = data["info"]
    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
    high_52w = info.get("fiftyTwoWeekHigh")
    low_52w = info.get("fiftyTwoWeekLow")
    pct_from_high = ((current_price / high_52w) - 1) * 100 if current_price and high_52w else None
    pct_from_low = ((current_price / low_52w) - 1) * 100 if current_price and low_52w else None

    financials = data["financials"]
    rev_growth = None
    earnings_growth = None
    if financials is not None and not financials.empty:
        if "Total Revenue" in financials.index:
            rev_sorted = financials.loc["Total Revenue"].dropna().sort_index()
            if len(rev_sorted) >= 2:
                rev_growth = (rev_sorted.iloc[-1] / rev_sorted.iloc[-2]) - 1
        if "Net Income" in financials.index:
            ni_sorted = financials.loc["Net Income"].dropna().sort_index()
            if len(ni_sorted) >= 2 and ni_sorted.iloc[-2] != 0:
                earnings_growth = (ni_sorted.iloc[-1] / ni_sorted.iloc[-2]) - 1

    metrics_rows.append({
        "Ticker": ticker,
        "Company": info.get("shortName", "N/A"),
        "Market Cap": info.get("marketCap"),
        "P/E Ratio": info.get("trailingPE"),
        "Forward P/E": info.get("forwardPE"),
        "EPS (TTM)": info.get("trailingEps"),
        "Earnings Growth (YoY)": earnings_growth,
        "Revenue (TTM)": info.get("totalRevenue"),
        "Revenue Growth (YoY)": rev_growth,
        "Profit Margin": info.get("profitMargins"),
        "Dividend Yield": info.get("trailingAnnualDividendYield"),
        "% From 52W High": pct_from_high,
        "% From 52W Low": pct_from_low,
    })

metrics_df = pd.DataFrame(metrics_rows).set_index("Ticker")


def fmt_large_number(val):
    if pd.isna(val) or val is None:
        return "N/A"
    if abs(val) >= 1e12:
        return f"${val / 1e12:.2f}T"
    if abs(val) >= 1e9:
        return f"${val / 1e9:.2f}B"
    if abs(val) >= 1e6:
        return f"${val / 1e6:.2f}M"
    return f"${val:,.0f}"


def fmt_pct(val):
    if pd.isna(val) or val is None:
        return "N/A"
    return f"{val * 100:.2f}%"


def fmt_number(val, prefix="", decimals=2):
    if pd.isna(val) or val is None:
        return "N/A"
    return f"{prefix}{val:,.{decimals}f}"


display_df = metrics_df.copy()
display_df["Market Cap"] = display_df["Market Cap"].apply(fmt_large_number)
display_df["Revenue (TTM)"] = display_df["Revenue (TTM)"].apply(fmt_large_number)
display_df["P/E Ratio"] = display_df["P/E Ratio"].apply(lambda x: fmt_number(x, decimals=1))
display_df["Forward P/E"] = display_df["Forward P/E"].apply(lambda x: fmt_number(x, decimals=1))
display_df["EPS (TTM)"] = display_df["EPS (TTM)"].apply(lambda x: fmt_number(x, prefix="$"))
display_df["Earnings Growth (YoY)"] = display_df["Earnings Growth (YoY)"].apply(fmt_pct)
display_df["Revenue Growth (YoY)"] = display_df["Revenue Growth (YoY)"].apply(fmt_pct)
display_df["Profit Margin"] = display_df["Profit Margin"].apply(fmt_pct)
display_df["Dividend Yield"] = display_df["Dividend Yield"].apply(fmt_pct)
display_df["% From 52W High"] = display_df["% From 52W High"].apply(lambda x: fmt_number(x, decimals=1) + "%" if x is not None and not pd.isna(x) else "N/A")
display_df["% From 52W Low"] = display_df["% From 52W Low"].apply(lambda x: fmt_number(x, decimals=1) + "%" if x is not None and not pd.isna(x) else "N/A")

st.dataframe(display_df, use_container_width=True)

# --- Price Chart ---
st.header("Stock Price History")

normalize = st.checkbox("Normalize prices (% change from start)", value=len(tickers) > 1)

fig_price = go.Figure()
for ticker, data in stock_data.items():
    hist = data["history"]
    if len(hist) > 0:
        pct_change = (hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100
        label = f"{ticker} ({pct_change:+.1f}%)"
        if normalize:
            values = (hist["Close"] / hist["Close"].iloc[0] - 1) * 100
            fig_price.add_trace(go.Scatter(
                x=hist.index, y=values, mode="lines", name=label,
            ))
        else:
            fig_price.add_trace(go.Scatter(
                x=hist.index, y=hist["Close"], mode="lines", name=label,
            ))

fig_price.update_layout(
    yaxis_title="% Change" if normalize else "Price (USD)",
    xaxis_title="Date",
    hovermode="x unified",
    height=500,
    template="plotly_white",
)
st.plotly_chart(fig_price, use_container_width=True)

# --- EPS Over Time ---
st.header("EPS Over Time (Quarterly)")

fig_eps = go.Figure()
for ticker, data in stock_data.items():
    eps_data = None
    ed = data["earnings_dates"]
    if ed is not None and not ed.empty and "Reported EPS" in ed.columns:
        eps_data = ed["Reported EPS"].dropna().sort_index()
        eps_data.index = eps_data.index.tz_localize(None) if eps_data.index.tz is None else eps_data.index.tz_convert(None)
    if eps_data is None or eps_data.empty:
        qi = data["quarterly_income_stmt"]
        if qi is not None and not qi.empty and "Diluted EPS" in qi.index:
            eps_data = qi.loc["Diluted EPS"].dropna().sort_index()
    if eps_data is not None and not eps_data.empty:
        eps_data = eps_data[(eps_data.index >= start_date) & (eps_data.index <= end_date)]
        eps_data = eps_data.sort_index()
        values = eps_data.values
        labels = [""]
        for i in range(1, len(values)):
            prev, curr = values[i - 1], values[i]
            if prev == 0:
                labels.append("N/A")
            elif prev < 0 and curr < 0:
                pct = (abs(prev) - abs(curr)) / abs(prev) * 100
                labels.append(f"{pct:+.1f}%")
            elif prev < 0 and curr >= 0:
                labels.append("Turned +")
            elif prev > 0 and curr < 0:
                labels.append("Turned -")
            else:
                pct = (curr / prev - 1) * 100
                labels.append(f"{pct:+.1f}%")
        fig_eps.add_trace(go.Bar(
            x=eps_data.index.strftime("%Y-%m"),
            y=eps_data.values,
            name=ticker,
            text=labels,
            textposition="outside",
        ))

fig_eps.update_layout(
    yaxis_title="EPS ($)",
    xaxis_title="Quarter",
    barmode="group",
    height=400,
    template="plotly_white",
    margin=dict(t=40),
    yaxis=dict(autorange=True, rangemode="tozero"),
)
fig_eps.update_yaxes(automargin=True, ticksuffix="  ")
fig_eps.update_traces(cliponaxis=False)
st.plotly_chart(fig_eps, use_container_width=True)

# --- P/E Over Time ---
st.header("P/E Ratio Over Time")

fig_pe = go.Figure()
for ticker, data in stock_data.items():
    hist = data["history"]
    if hist.empty:
        continue
    eps_data = None
    ed = data["earnings_dates"]
    if ed is not None and not ed.empty and "Reported EPS" in ed.columns:
        eps_data = ed["Reported EPS"].dropna().sort_index()
        eps_data.index = eps_data.index.tz_localize(None) if eps_data.index.tz is None else eps_data.index.tz_convert(None)
    if eps_data is None or eps_data.empty:
        qi = data["quarterly_income_stmt"]
        if qi is not None and not qi.empty and "Diluted EPS" in qi.index:
            eps_data = qi.loc["Diluted EPS"].dropna().sort_index()
    if eps_data is not None and not eps_data.empty:
        eps_data = eps_data.sort_index()
        ttm_eps = eps_data.rolling(4).sum().dropna()
        if len(ttm_eps) < 1:
            continue
        hist_dates = hist.index.tz_localize(None) if hist.index.tz else hist.index
        pe_series = []
        for trade_date, price in zip(hist_dates, hist["Close"]):
            past_eps = ttm_eps[ttm_eps.index <= trade_date]
            if len(past_eps) > 0 and past_eps.iloc[-1] > 0:
                pe_series.append({"date": trade_date, "pe": price / past_eps.iloc[-1]})
        if pe_series:
            pe_df = pd.DataFrame(pe_series)
            fig_pe.add_trace(go.Scatter(
                x=pe_df["date"], y=pe_df["pe"],
                mode="lines", name=ticker,
            ))

fig_pe.update_layout(
    yaxis_title="P/E Ratio",
    xaxis_title="Date",
    hovermode="x unified",
    height=400,
    template="plotly_white",
)
st.plotly_chart(fig_pe, use_container_width=True)

# --- Revenue & Earnings Charts ---
st.header("Revenue & Earnings")

rev_col, earn_col = st.columns(2)

with rev_col:
    st.subheader("Annual Revenue")
    fig_rev = go.Figure()
    for ticker, data in stock_data.items():
        financials = data["financials"]
        if financials is not None and not financials.empty and "Total Revenue" in financials.index:
            revenue = financials.loc["Total Revenue"].dropna().sort_index()
            growth = revenue.pct_change() * 100
            labels = [f"{g:+.1f}%" if pd.notna(g) else "" for g in growth.values]
            fig_rev.add_trace(go.Bar(
                x=revenue.index.strftime("%Y"),
                y=revenue.values,
                name=ticker,
                text=labels,
                textposition="outside",
            ))
    fig_rev.update_layout(
        yaxis_title="Revenue (USD)",
        barmode="group",
        height=400,
        template="plotly_white",
        margin=dict(t=40),
        yaxis=dict(autorange=True, rangemode="tozero"),
    )
    fig_rev.update_yaxes(automargin=True, ticksuffix="  ")
    fig_rev.update_traces(cliponaxis=False)
    st.plotly_chart(fig_rev, use_container_width=True)

with earn_col:
    st.subheader("Annual Net Income")
    fig_earn = go.Figure()
    for ticker, data in stock_data.items():
        financials = data["financials"]
        if financials is not None and not financials.empty and "Net Income" in financials.index:
            net_income = financials.loc["Net Income"].dropna().sort_index()
            values = net_income.values
            labels = [""]
            for i in range(1, len(values)):
                prev, curr = values[i - 1], values[i]
                if prev == 0:
                    labels.append("N/A")
                elif prev < 0 and curr < 0:
                    pct = (abs(prev) - abs(curr)) / abs(prev) * 100
                    labels.append(f"{pct:+.1f}%")
                elif prev < 0 and curr >= 0:
                    labels.append("Turned Profitable")
                elif prev > 0 and curr < 0:
                    labels.append("Turned Negative")
                else:
                    pct = (curr / prev - 1) * 100
                    labels.append(f"{pct:+.1f}%")
            fig_earn.add_trace(go.Bar(
                x=net_income.index.strftime("%Y"),
                y=net_income.values,
                name=ticker,
                text=labels,
                textposition="outside",
            ))
    fig_earn.update_layout(
        yaxis_title="Net Income (USD)",
        barmode="group",
        height=400,
        template="plotly_white",
        margin=dict(t=40),
        yaxis=dict(autorange=True, rangemode="tozero"),
    )
    fig_earn.update_yaxes(automargin=True, ticksuffix="  ")
    fig_earn.update_traces(cliponaxis=False)
    st.plotly_chart(fig_earn, use_container_width=True)

# --- Total Debt ---
st.header("Total Debt")

fig_debt = go.Figure()
for ticker, data in stock_data.items():
    bs = data["balance_sheet"]
    if bs is not None and not bs.empty and "Total Debt" in bs.index:
        debt = bs.loc["Total Debt"].dropna().sort_index()
        labels = []
        for date in debt.index:
            equity = None
            if "Stockholders Equity" in bs.index:
                eq_val = bs.loc["Stockholders Equity"].get(date)
                if pd.notna(eq_val) and eq_val != 0:
                    equity = eq_val
            if equity is not None:
                de_ratio = debt[date] / equity
                labels.append(f"D/E: {de_ratio:.2f}")
            else:
                labels.append("")
        fig_debt.add_trace(go.Bar(
            x=debt.index.strftime("%Y"),
            y=debt.values,
            name=ticker,
            text=labels,
            textposition="outside",
        ))

fig_debt.update_layout(
    yaxis_title="Total Debt (USD)",
    barmode="group",
    height=400,
    template="plotly_white",
    margin=dict(t=40),
    yaxis=dict(autorange=True, rangemode="tozero"),
)
fig_debt.update_yaxes(automargin=True, ticksuffix="  ")
fig_debt.update_traces(cliponaxis=False)
st.plotly_chart(fig_debt, use_container_width=True)
st.caption("A D/E ratio greater than 1.0 generally indicates debt exceeds equity.")

# --- Profit Margin Over Time ---
st.header("Profit Margin Over Time")

fig_margin = go.Figure()
for ticker, data in stock_data.items():
    financials = data["financials"]
    if financials is not None and not financials.empty:
        has_revenue = "Total Revenue" in financials.index
        has_income = "Net Income" in financials.index
        if has_revenue and has_income:
            revenue = financials.loc["Total Revenue"].dropna().sort_index()
            net_income = financials.loc["Net Income"].dropna().sort_index()
            common_idx = revenue.index.intersection(net_income.index)
            if len(common_idx) > 0:
                margin = (net_income[common_idx] / revenue[common_idx]) * 100
                fig_margin.add_trace(go.Scatter(
                    x=common_idx,
                    y=margin.values,
                    mode="lines+markers",
                    name=ticker,
                ))

fig_margin.update_layout(
    yaxis_title="Net Profit Margin (%)",
    xaxis_title="Date",
    height=400,
    template="plotly_white",
)
st.plotly_chart(fig_margin, use_container_width=True)

# --- Quarterly Revenue Trend ---
st.header("Quarterly Revenue Trend")

fig_qrev = go.Figure()
for ticker, data in stock_data.items():
    qf = data["quarterly_financials"]
    if qf is not None and not qf.empty and "Total Revenue" in qf.index:
        q_revenue = qf.loc["Total Revenue"].dropna().sort_index()
        fig_qrev.add_trace(go.Scatter(
            x=q_revenue.index,
            y=q_revenue.values,
            mode="lines+markers",
            name=ticker,
        ))

fig_qrev.update_layout(
    yaxis_title="Revenue (USD)",
    xaxis_title="Quarter",
    height=400,
    template="plotly_white",
)
st.plotly_chart(fig_qrev, use_container_width=True)

# --- Dividend Yield Comparison ---
st.header("Dividend Comparison")

div_data = []
for ticker, data in stock_data.items():
    info = data["info"]
    div_data.append({
        "Ticker": ticker,
        "Dividend Yield (%)": (info.get("trailingAnnualDividendYield") or 0) * 100,
        "Dividend Rate ($)": info.get("dividendRate") or 0,
        "Payout Ratio (%)": (info.get("payoutRatio") or 0) * 100,
    })

div_df = pd.DataFrame(div_data)

if div_df["Dividend Yield (%)"].sum() > 0:
    dcol1, dcol2 = st.columns(2)
    with dcol1:
        fig_dy = px.bar(
            div_df, x="Ticker", y="Dividend Yield (%)",
            color="Ticker", title="Dividend Yield",
            template="plotly_white",
        )
        fig_dy.update_layout(height=350, showlegend=False)
        st.plotly_chart(fig_dy, use_container_width=True)

    with dcol2:
        fig_pr = px.bar(
            div_df, x="Ticker", y="Payout Ratio (%)",
            color="Ticker", title="Payout Ratio",
            template="plotly_white",
        )
        fig_pr.update_layout(height=350, showlegend=False)
        st.plotly_chart(fig_pr, use_container_width=True)
else:
    st.info("None of the selected stocks currently pay dividends.")

st.caption("Data provided by Yahoo Finance via yfinance. Metrics may be delayed or unavailable for some tickers.")
