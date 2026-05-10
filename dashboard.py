"""
Payment TPV Explorer dashboard.
Run with: streamlit run dashboard.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from sqlalchemy import create_engine, text
from datetime import date, timedelta

st.set_page_config(page_title="Payment TPV Explorer", layout="wide")

DB_URL  = "postgresql://postgres:olist123@localhost:5432/transactions"
TODAY   = date(2026, 5, 10)

PRODUCT_COLORS = {
    "regular_ach": "#1f77b4",
    "check":       "#ff7f0e",
    "two_day_ach": "#2ca02c",
    "one_day_ach": "#d62728",
}
ALL_PRODUCTS = list(PRODUCT_COLORS.keys())
SETTLE_DAYS  = {"regular_ach": 7, "check": 14, "two_day_ach": 7, "one_day_ach": 7}


# ── Data loaders ──────────────────────────────────────────────────────────────

@st.cache_resource
def get_engine():
    return create_engine(DB_URL)


@st.cache_data(ttl=120)
def load_daily(products: tuple, start: str, end: str) -> pd.DataFrame:
    placeholders = ", ".join(f"'{p}'" for p in products)
    df = pd.read_sql(text(f"""
        SELECT date, product,
               SUM(tpv_scheduled)           AS tpv,
               SUM(payment_count_scheduled)  AS pmt_count,
               BOOL_AND(is_complete)         AS is_complete
        FROM payment_daily_tpv
        WHERE product IN ({placeholders})
          AND date BETWEEN '{start}' AND '{end}'
        GROUP BY date, product
        ORDER BY date, product
    """), get_engine())
    df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=120)
def load_anomaly_events() -> pd.DataFrame:
    return pd.read_sql(
        "SELECT * FROM anomaly_ground_truth ORDER BY start_date",
        get_engine(),
    )


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Filters")

    selected = st.multiselect(
        "Products",
        options=ALL_PRODUCTS,
        default=ALL_PRODUCTS,
        format_func=lambda x: x.replace("_", " ").title(),
    )

    date_range = st.date_input(
        "Date range",
        value=(date(2022, 1, 1), TODAY),
        min_value=date(2022, 1, 1),
        max_value=TODAY,
    )

    metric = st.radio("Metric", ["Daily TPV ($)", "Payment Count"])

    st.divider()
    st.subheader("Overlays")
    show_anomalies  = st.checkbox("Anomaly events",       value=True)
    show_settlement = st.checkbox("Settlement cutoffs",   value=True)
    show_smoothed   = st.checkbox("7-day rolling average", value=True)


# ── Guards ────────────────────────────────────────────────────────────────────

st.title("Payment TPV Explorer")

if not selected:
    st.warning("Select at least one product from the sidebar.")
    st.stop()

if len(date_range) != 2:
    st.stop()

start_date, end_date = date_range

if start_date >= end_date:
    st.error("Start date must be before end date.")
    st.stop()


# ── Load data ─────────────────────────────────────────────────────────────────

df = load_daily(tuple(selected), str(start_date), str(end_date))

if df.empty:
    st.warning("No data found for this selection.")
    st.stop()


# ── Trend chart ───────────────────────────────────────────────────────────────

y_col    = "tpv"      if metric == "Daily TPV ($)" else "pmt_count"
y_label  = "TPV ($)"  if metric == "Daily TPV ($)" else "Payment Count"
y_format = "$,.0f"    if metric == "Daily TPV ($)" else ",.0f"

fig = go.Figure()

for product in selected:
    pdata    = df[df["product"] == product].sort_values("date").copy()
    complete = pdata[pdata["is_complete"]]
    partial  = pdata[~pdata["is_complete"]]
    color    = PRODUCT_COLORS[product]
    label    = product.replace("_", " ").title()

    # Solid line — settled / complete data
    fig.add_trace(go.Scatter(
        x=complete["date"], y=complete[y_col],
        name=label,
        line=dict(color=color, width=1.2),
        hovertemplate=f"%{{x|%b %d %Y}}<br>{y_label}: %{{y:{y_format}}}<extra>{label}</extra>",
    ))

    # Dotted line — partial / unsettled data
    if not partial.empty:
        fig.add_trace(go.Scatter(
            x=partial["date"], y=partial[y_col],
            name=f"{label} (partial)",
            line=dict(color=color, width=1.2, dash="dot"),
            showlegend=False,
            hovertemplate=f"%{{x|%b %d %Y}}<br>{y_label}: %{{y:{y_format}}} (partial)<extra>{label}</extra>",
        ))

    # 7-day rolling average
    if show_smoothed and len(complete) >= 7:
        pdata_c = complete.copy()
        pdata_c["smoothed"] = pdata_c[y_col].rolling(7, center=True, min_periods=4).mean()
        fig.add_trace(go.Scatter(
            x=pdata_c["date"], y=pdata_c["smoothed"],
            name=f"{label} (7d avg)",
            line=dict(color=color, width=2.5, dash="dash"),
            opacity=0.65,
            hoverinfo="skip",
        ))

# Settlement cutoff markers
if show_settlement:
    unique_products = set(selected)
    ach_products   = unique_products - {"check"}
    check_products = unique_products & {"check"}

    if ach_products:
        cutoff = pd.Timestamp(TODAY - timedelta(days=7))
        if pd.Timestamp(start_date) <= cutoff <= pd.Timestamp(end_date):
            fig.add_vline(
                x=cutoff.timestamp() * 1000,
                line_dash="dash", line_color="steelblue", opacity=0.6,
                annotation_text="ACH settle cutoff (−7d)",
                annotation_position="top left",
                annotation_font_size=11,
            )
    if check_products:
        cutoff = pd.Timestamp(TODAY - timedelta(days=14))
        if pd.Timestamp(start_date) <= cutoff <= pd.Timestamp(end_date):
            fig.add_vline(
                x=cutoff.timestamp() * 1000,
                line_dash="dash", line_color="darkorange", opacity=0.6,
                annotation_text="Check settle cutoff (−14d)",
                annotation_position="top left",
                annotation_font_size=11,
            )

# Anomaly event bands
if show_anomalies:
    events = load_anomaly_events()
    for _, ev in events.iterrows():
        ev_start = pd.Timestamp(ev["start_date"])
        ev_end   = pd.Timestamp(ev["end_date"])
        if ev_start > pd.Timestamp(end_date) or ev_end < pd.Timestamp(start_date):
            continue
        # Only shade if the event affects a selected product
        affected = [p.strip() for p in ev["affected_products"].split(",")]
        if not any(p in selected for p in affected):
            continue
        fig.add_vrect(
            x0=ev_start, x1=ev_end,
            fillcolor="red", opacity=0.07, line_width=0,
            annotation_text=ev["event_name"].replace("_", " "),
            annotation_position="top left",
            annotation_font_size=9,
        )

fig.update_layout(
    height=520,
    xaxis_title="Date",
    yaxis_title=y_label,
    yaxis_tickformat=y_format,
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    margin=dict(t=60, r=20),
    plot_bgcolor="white",
    xaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
    yaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
)

st.plotly_chart(fig, use_container_width=True)


# ── Period summary table ───────────────────────────────────────────────────────

st.subheader("Period summary  *(complete data only)*")

summary = (
    df[df["is_complete"]]
    .groupby("product")
    .agg(
        total_tpv   =("tpv",       "sum"),
        avg_daily   =("tpv",       "mean"),
        total_pmts  =("pmt_count", "sum"),
        days        =("date",      "count"),
    )
    .reindex([p for p in ALL_PRODUCTS if p in df["product"].unique()])
    .reset_index()
)

summary["product"]    = summary["product"].str.replace("_", " ").str.title()
summary["total_tpv"]  = summary["total_tpv"].map("${:,.0f}".format)
summary["avg_daily"]  = summary["avg_daily"].map("${:,.0f}".format)
summary["total_pmts"] = summary["total_pmts"].map("{:,}".format)
summary = summary.rename(columns={
    "product":   "Product",
    "total_tpv": "Total TPV",
    "avg_daily": "Avg Daily TPV",
    "total_pmts":"Total Payments",
    "days":      "Days",
})

st.dataframe(summary, use_container_width=True, hide_index=True)


# ── Anomaly event reference ───────────────────────────────────────────────────

if show_anomalies:
    with st.expander("Injected anomaly events (ground truth reference)"):
        events = load_anomaly_events()
        events_display = events[[
            "event_id", "event_name", "start_date", "end_date",
            "affected_products", "direction", "description",
        ]].copy()
        events_display["event_name"] = events_display["event_name"].str.replace("_", " ")
        st.dataframe(events_display, use_container_width=True, hide_index=True)
