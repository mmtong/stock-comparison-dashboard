import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
import requests
from datetime import datetime, timedelta

st.set_page_config(page_title="Stock Dashboard", layout="wide")

st.title("Stock Dashboard")
st.caption(f"Data as of {datetime.today().strftime('%B %d, %Y')}")

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

# Shared x-axis window used by Stock Price History, EPS Over Time, and P/E Over Time.
# All three charts MUST lock to this range so they share the same time period and scale.
shared_x_range = [start_date, end_date]


# Load Alpha Vantage API key from Streamlit secrets (set in .streamlit/secrets.toml
# locally and in the Streamlit Cloud dashboard under App Settings > Secrets).
ALPHAVANTAGE_API_KEY = st.secrets.get("ALPHAVANTAGE_API_KEY", "")


@st.cache_data(ttl=300)
def fetch_av_eps(ticker):
    """Fetch full quarterly reported-EPS history from Alpha Vantage.
    Returns (series_or_None, status_message).

    Free tier is 25 calls/day; we make one call per ticker (cached 5 min).
    Alpha Vantage signals problems with an 'Information', 'Note', or
    'Error Message' key instead of 'quarterlyEarnings' — we surface that text
    so the UI can explain why it fell back to yfinance."""
    if not ALPHAVANTAGE_API_KEY or ALPHAVANTAGE_API_KEY == "your_alphavantage_api_key_here":
        return None, "no API key configured"
    try:
        url = (
            f"https://www.alphavantage.co/query?function=EARNINGS"
            f"&symbol={ticker}&apikey={ALPHAVANTAGE_API_KEY}"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        quarters = data.get("quarterlyEarnings")
        if not quarters:
            # Surface AV's own message (rate limit, premium notice, bad symbol).
            note = data.get("Information") or data.get("Note") or data.get("Error Message")
            if note:
                return None, str(note)[:200]
            return None, "no quarterly data returned"
        df = pd.DataFrame(quarters)
        df["reportedEPS"] = pd.to_numeric(df["reportedEPS"], errors="coerce")
        df = df[df["reportedEPS"].notna()]
        if df.empty:
            return None, "no parseable EPS values"
        df["fiscalDateEnding"] = pd.to_datetime(df["fiscalDateEnding"])
        df = df.set_index("fiscalDateEnding").sort_index()
        return df["reportedEPS"].rename(ticker), "ok"
    except Exception as e:
        return None, f"request failed: {e}"


@st.cache_data(ttl=300)
def fetch_stock_data(ticker, start, end):
    stock = yf.Ticker(ticker)
    hist = stock.history(start=start, end=end)
    info = stock.info
    financials = stock.financials
    quarterly_financials = stock.quarterly_financials
    quarterly_income_stmt = stock.quarterly_income_stmt
    income_stmt = stock.income_stmt
    # get_earnings_dates is the only LONG quarterly EPS source (~10+ yrs) but its
    # scraping endpoint is often blocked from datacenter IPs (e.g. Streamlit Cloud).
    # Retry a couple times; if it still fails we fall back to annual income_stmt.
    earnings_dates = None
    for _ in range(3):
        try:
            ed = stock.get_earnings_dates(limit=60)
            if ed is not None and not ed.empty:
                earnings_dates = ed
                break
        except Exception:
            pass
    balance_sheet = stock.balance_sheet
    return hist, info, financials, quarterly_financials, quarterly_income_stmt, income_stmt, earnings_dates, balance_sheet


def build_eps_series(data):
    """Return (eps_series, is_quarterly, source_label) using the longest source.

    Priority:
      1. Alpha Vantage - long quarterly history (20+ yrs), works on Cloud.
                     Requires ALPHAVANTAGE_API_KEY in Streamlit secrets.
      2. yfinance get_earnings_dates - long quarterly history but often blocked
                     from datacenter IPs (Streamlit Cloud).
      3. income_stmt (annual) - ~4 yrs, reliable fallback on Cloud.
                     Already a 12-month figure — must NOT be re-rolled for TTM.
      4. quarterly_income_stmt - only ~5 recent quarters, last resort."""
    # 1. Alpha Vantage
    av = data.get("av_eps")
    if av is not None and not av.empty:
        return av, True, "Alpha Vantage (quarterly)"

    # 2. yfinance get_earnings_dates
    ed = data["earnings_dates"]
    if ed is not None and not ed.empty and "Reported EPS" in ed.columns:
        s = ed["Reported EPS"].dropna().sort_index()
        s.index = s.index.tz_localize(None) if s.index.tz is None else s.index.tz_convert(None)
        if not s.empty:
            return s, True, "yfinance earnings dates (quarterly)"

    # 3. Annual income statement
    ist = data["income_stmt"]
    if ist is not None and not ist.empty and "Diluted EPS" in ist.index:
        s = ist.loc["Diluted EPS"].dropna().sort_index()
        if not s.empty:
            return s, False, "yfinance income statement (annual fallback)"

    # 4. Short quarterly fallback
    qi = data["quarterly_income_stmt"]
    if qi is not None and not qi.empty and "Diluted EPS" in qi.index:
        s = qi.loc["Diluted EPS"].dropna().sort_index()
        if not s.empty:
            return s, True, "yfinance quarterly income statement (5 quarters)"

    return None, True, "unavailable"


with st.spinner("Fetching stock data..."):
    stock_data = {}
    failed = []
    for ticker in tickers:
        try:
            hist, info, financials, quarterly_financials, quarterly_income_stmt, income_stmt, earnings_dates, balance_sheet = fetch_stock_data(
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
                "income_stmt": income_stmt,
                "earnings_dates": earnings_dates,
                "balance_sheet": balance_sheet,
            }
            av_series, av_status = fetch_av_eps(ticker)
            stock_data[ticker]["av_eps"] = av_series
            stock_data[ticker]["av_status"] = av_status
        except Exception as e:
            failed.append(f"{ticker} ({e})")

if failed:
    st.error(f"Could not fetch data for: {', '.join(failed)}")

if not stock_data:
    st.warning("No valid stock data found. Check your ticker symbols.")
    st.stop()


def ticker_label(ticker, info=None):
    """Return 'Company Name (TICKER)' for chart legends, or just the ticker
    if no company name is available."""
    if info is None:
        info = stock_data.get(ticker, {}).get("info", {}) or {}
    name = info.get("shortName") or info.get("longName")
    return f"{name} ({ticker})" if name else ticker


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

transposed_df = display_df.T
transposed_df.columns = [f"{display_df.loc[t, 'Company']} ({t})" for t in transposed_df.columns]
transposed_df = transposed_df.drop("Company")

html = '<table style="width:100%; border-collapse:collapse;">'
html += '<tr style="border-bottom:2px solid #ddd;"><th style="text-align:left; padding:8px;"></th>'
for col in transposed_df.columns:
    html += f'<th style="text-align:center; padding:8px;">{col}</th>'
html += '</tr>'
for row_label, row_data in transposed_df.iterrows():
    html += f'<tr style="border-bottom:1px solid #eee;"><td style="text-align:left; padding:8px; font-weight:600;">{row_label}</td>'
    for val in row_data:
        html += f'<td style="text-align:center; padding:8px;">{val}</td>'
    html += '</tr>'
html += '</table>'
st.markdown(html, unsafe_allow_html=True)

# --- Price Chart ---
st.header("Stock Price History")

normalize = st.checkbox("Normalize prices (% change from start)", value=len(tickers) > 1)

fig_price = go.Figure()
for ticker, data in stock_data.items():
    hist = data["history"]
    if len(hist) > 0:
        x_dates = hist.index.tz_localize(None) if hist.index.tz else hist.index
        pct_change = (hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100
        label = f"{ticker_label(ticker)}: {pct_change:+.1f}%"
        if normalize:
            values = (hist["Close"] / hist["Close"].iloc[0] - 1) * 100
            fig_price.add_trace(go.Scatter(
                x=x_dates, y=values, mode="lines", name=label,
            ))
        else:
            fig_price.add_trace(go.Scatter(
                x=x_dates, y=hist["Close"], mode="lines", name=label,
            ))

fig_price.update_layout(
    yaxis_title="% Change" if normalize else "Price (USD)",
    xaxis_title="Date",
    hovermode="x unified",
    height=500,
    template="plotly_white",
    xaxis=dict(range=shared_x_range, type="date"),
)
st.plotly_chart(fig_price, use_container_width=True)

# --- EPS Over Time ---
st.header("EPS Over Time (Quarterly)")

fig_eps = go.Figure()
eps_sources = {}
for ticker, data in stock_data.items():
    eps_data, _, source_label = build_eps_series(data)
    eps_sources[ticker] = (source_label, data.get("av_status", ""))
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
            x=eps_data.index,
            y=eps_data.values,
            name=ticker_label(ticker),
            text=labels,
            textposition="outside",
        ))

fig_eps.update_layout(
    yaxis_title="EPS ($)",
    xaxis_title="Date",
    barmode="group",
    height=400,
    template="plotly_white",
    margin=dict(t=40),
    yaxis=dict(autorange=True, rangemode="tozero"),
    xaxis=dict(range=shared_x_range, type="date"),
)
fig_eps.update_yaxes(automargin=True, ticksuffix="  ")
fig_eps.update_traces(cliponaxis=False)
st.plotly_chart(fig_eps, use_container_width=True)

# Show which EPS source each ticker used (and why, if Alpha Vantage was skipped).
source_lines = []
for ticker, (label, av_status) in eps_sources.items():
    if "Alpha Vantage" in label:
        source_lines.append(f"**{ticker_label(ticker)}**: {label}")
    else:
        extra = f" — Alpha Vantage unavailable: {av_status}" if av_status else ""
        source_lines.append(f"**{ticker_label(ticker)}**: {label}{extra}")
st.caption("EPS data source — " + "  |  ".join(source_lines))

# --- P/E Over Time ---
st.header("P/E Ratio Over Time")

fig_pe = go.Figure()
for ticker, data in stock_data.items():
    hist = data["history"]
    if hist.empty:
        continue
    eps_data, is_quarterly, _ = build_eps_series(data)
    if eps_data is not None and not eps_data.empty:
        eps_data = eps_data.sort_index()
        # Quarterly EPS must be summed over 4 quarters for a TTM figure; annual
        # EPS is already a 12-month figure and is used as-is.
        ttm_eps = eps_data.rolling(4).sum().dropna() if is_quarterly else eps_data
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
                mode="lines", name=ticker_label(ticker),
            ))

fig_pe.update_layout(
    yaxis_title="P/E Ratio",
    xaxis_title="Date",
    hovermode="x unified",
    height=400,
    template="plotly_white",
    xaxis=dict(range=shared_x_range, type="date"),
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
                name=ticker_label(ticker),
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
                name=ticker_label(ticker),
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
            name=ticker_label(ticker),
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
                    name=ticker_label(ticker),
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
            name=ticker_label(ticker),
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
        "Label": ticker_label(ticker, info),
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
            color="Label", title="Dividend Yield",
            template="plotly_white",
        )
        fig_dy.update_layout(height=350, legend_title_text="")
        st.plotly_chart(fig_dy, use_container_width=True)

    with dcol2:
        fig_pr = px.bar(
            div_df, x="Ticker", y="Payout Ratio (%)",
            color="Label", title="Payout Ratio",
            template="plotly_white",
        )
        fig_pr.update_layout(height=350, legend_title_text="")
        st.plotly_chart(fig_pr, use_container_width=True)
else:
    st.info("None of the selected stocks currently pay dividends.")

# --- Single-Company Deep Dive: Price + P/E + EPS (stacked panels) ---
st.header("Drivers of Stock Price (EPS x P/E)")
st.caption("Price, P/E, and EPS for one company over its own selected period.")

dd_col_input, dd_col_range = st.columns([3, 1])
with dd_col_input:
    deep_ticker_input = st.text_input(
        "Enter one ticker for the combined view",
        value=tickers[0] if tickers else "COST",
        key="deep_dive_ticker",
    ).strip().upper()
with dd_col_range:
    dd_time_range = st.selectbox(
        "Time range",
        ["1M", "3M", "6M", "1Y", "2Y", "5Y", "10Y"],
        index=5,
        key="deep_dive_range",
    )

dd_end_date = datetime.today()
dd_start_date = dd_end_date - timedelta(days=range_map[dd_time_range])
dd_x_range = [dd_start_date, dd_end_date]

if deep_ticker_input:
    try:
        dd_hist, dd_info, dd_fin, dd_qfin, dd_qis, dd_is, dd_ed, dd_bs = fetch_stock_data(
            deep_ticker_input, dd_start_date, dd_end_date
        )
    except Exception as e:
        dd_hist = None
        st.error(f"Could not fetch data for {deep_ticker_input}: {e}")

    if dd_hist is not None and not dd_hist.empty:
        dd_av_series, _ = fetch_av_eps(deep_ticker_input)
        dd_data = {
            "earnings_dates": dd_ed,
            "income_stmt": dd_is,
            "quarterly_income_stmt": dd_qis,
            "av_eps": dd_av_series,
        }
        eps_series, is_quarterly, source_label = build_eps_series(dd_data)

        fig_dd = make_subplots(
            rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
            row_heights=[0.4, 0.3, 0.3],
            subplot_titles=("Stock Price (USD)", "P/E Ratio (TTM)", "EPS"),
        )

        # Row 1: daily closing price
        dd_dates = dd_hist.index.tz_localize(None) if dd_hist.index.tz else dd_hist.index
        fig_dd.add_trace(
            go.Scatter(x=dd_dates, y=dd_hist["Close"], mode="lines",
                       name="Price", line=dict(color="#1f77b4")),
            row=1, col=1,
        )

        # Rows 2 & 3: P/E (daily price / TTM EPS) and EPS bars
        if eps_series is not None and not eps_series.empty:
            eps_series = eps_series.sort_index()
            ttm_eps = eps_series.rolling(4).sum().dropna() if is_quarterly else eps_series

            pe_dates, pe_vals = [], []
            for trade_date, price in zip(dd_dates, dd_hist["Close"]):
                past = ttm_eps[ttm_eps.index <= trade_date]
                if len(past) > 0 and past.iloc[-1] > 0:
                    pe_dates.append(trade_date)
                    pe_vals.append(price / past.iloc[-1])
            if pe_vals:
                fig_dd.add_trace(
                    go.Scatter(x=pe_dates, y=pe_vals, mode="lines",
                               name="P/E", line=dict(color="#ff7f0e")),
                    row=2, col=1,
                )

            eps_windowed = eps_series[(eps_series.index >= dd_start_date) & (eps_series.index <= dd_end_date)]
            dd_vals = eps_windowed.values
            dd_labels = [""]
            for i in range(1, len(dd_vals)):
                prev, curr = dd_vals[i - 1], dd_vals[i]
                if prev == 0:
                    dd_labels.append("N/A")
                elif prev < 0 and curr < 0:
                    dd_labels.append(f"{(abs(prev) - abs(curr)) / abs(prev) * 100:+.1f}%")
                elif prev < 0 and curr >= 0:
                    dd_labels.append("Turned +")
                elif prev > 0 and curr < 0:
                    dd_labels.append("Turned -")
                else:
                    dd_labels.append(f"{(curr / prev - 1) * 100:+.1f}%")
            fig_dd.add_trace(
                go.Bar(x=eps_windowed.index, y=eps_windowed.values,
                       name="EPS", marker_color="#2ca02c",
                       text=dd_labels, textposition="outside", cliponaxis=False),
                row=3, col=1,
            )

        fig_dd.update_layout(
            height=750,
            template="plotly_white",
            hovermode="x unified",
            showlegend=False,
            margin=dict(t=40),
        )
        # Lock every panel's x-axis to this section's own selected window.
        fig_dd.update_xaxes(range=dd_x_range, type="date")
        fig_dd.update_xaxes(title_text="Date", row=3, col=1)
        st.plotly_chart(fig_dd, use_container_width=True)
        st.caption(f"{ticker_label(deep_ticker_input, dd_info)} — EPS source: {source_label}")
    elif dd_hist is not None:
        st.warning(f"No data found for {deep_ticker_input}.")

st.caption("Data provided by Yahoo Finance via yfinance. Metrics may be delayed or unavailable for some tickers.")
