"""SEO Log File Analyzer — Streamlit dashboard.

Run:
    streamlit run app.py
"""
from __future__ import annotations

import os
import tempfile

import pandas as pd
import streamlit as st

from analyzer import bots, charts, geo, metrics
from analyzer.parser import ALL_FORMATS, parse_log

st.set_page_config(page_title="SEO Log File Analyzer", layout="wide", page_icon="🪵")

st.markdown(
    """
    <style>
      /* Tighter, smaller metric cards */
      [data-testid="stMetricValue"] { font-size: 1.35rem; line-height: 1.2; }
      [data-testid="stMetricLabel"] p { font-size: 0.78rem; opacity: 0.7; }
      [data-testid="stMetric"] { padding: 0.15rem 0; }
      /* Smaller page title */
      h1 { font-size: 1.9rem !important; }
      /* Reduce gap between metric rows */
      div[data-testid="stHorizontalBlock"] { gap: 0.6rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def _parse(path: str, fmt: str, regex: str | None, fields: tuple | None, unit: str | None):
    res = parse_log(
        path, fmt,
        custom_regex=regex,
        custom_fields=list(fields) if fields else None,
        time_unit=unit,
    )
    return res.df, res.errors, res.n_lines, res.n_errors


@st.cache_data(show_spinner=False)
def _verify(df: pd.DataFrame):
    return bots.verify_bots(df)


@st.cache_data(show_spinner=False)
def _geo(df: pd.DataFrame):
    return geo.add_countries(df)


def metric_grid(ov: dict):
    c = st.columns(4)
    c[0].metric("Unique URLs", f"{ov['unique_urls']:,}")
    c[1].metric("Total Events", f"{ov['total_events']:,}")
    c[2].metric("Events / Day", ov["events_per_day"])
    c[3].metric("Total Bytes", f"{ov['total_bytes']:,}")
    c = st.columns(4)
    c[0].metric("Avg Bytes", f"{ov['average_bytes']:,}")
    c[1].metric("Total CO2 (mg)", f"{ov['total_co2_mg']:,}")
    att = ov["average_time_taken_ms"]
    c[2].metric("Avg Time (ms)", f"{att:,}" if att is not None else "n/a")
    c[3].metric("Errors", f"{ov['errors']:,} ({ov['errors_pct']}%)")
    c = st.columns(5)
    c[0].metric("1xx", f"{ov['provisional']} ({ov['provisional_pct']}%)")
    c[1].metric("2xx", f"{ov['success']} ({ov['success_pct']}%)")
    c[2].metric("3xx", f"{ov['redirection']} ({ov['redirection_pct']}%)")
    c[3].metric("4xx", f"{ov['client_error']} ({ov['client_error_pct']}%)")
    c[4].metric("5xx", f"{ov['server_error']} ({ov['server_error_pct']}%)")


# ------------------------------------------------------------------ sidebar
st.sidebar.title("🪵 Log Analyzer")
st.sidebar.caption("SEO log file analysis — Screaming Frog style")

uploaded = st.sidebar.file_uploader("Upload access log", type=None)
default_path = "sample_logs/access.log"
use_sample = st.sidebar.checkbox("Use bundled sample log", value=not uploaded)

fmt_labels = {k: v["label"] for k, v in ALL_FORMATS.items()}
fmt = st.sidebar.selectbox(
    "Log format", options=list(ALL_FORMATS.keys()) + ["custom"],
    format_func=lambda k: fmt_labels.get(k, "Custom regex"),
    index=list(ALL_FORMATS.keys()).index("combined_time"),
)

custom_regex = custom_fields = custom_unit = None
if fmt == "custom":
    custom_regex = st.sidebar.text_area("Custom regex (named groups)", height=120)
    fields_str = st.sidebar.text_input("Fields (comma-separated)")
    custom_fields = tuple(f.strip() for f in fields_str.split(",") if f.strip()) if fields_str else None
    custom_unit = st.sidebar.selectbox("Time unit (if any)", [None, "ms", "us"], format_func=lambda x: x or "none")

do_verify = st.sidebar.checkbox("Verify bots (reverse DNS)", value=False, help="Slower — network lookups, cached")
do_geo = st.sidebar.checkbox("Geolocate IPs (ip-api.com)", value=False, help="Slower — network lookups, cached")

# ------------------------------------------------------------------ resolve input
log_path = None
if uploaded is not None and not use_sample:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    tmp.write(uploaded.getvalue())
    tmp.close()
    log_path = tmp.name
elif use_sample and os.path.exists(default_path):
    log_path = default_path

if not log_path:
    st.info("Upload a log file or tick **Use bundled sample log** in the sidebar.")
    st.stop()

with st.spinner("Parsing log…"):
    df, errors, n_lines, n_errors = _parse(
        log_path, fmt, custom_regex, custom_fields, custom_unit
    )

if df.empty:
    st.error("No rows parsed. Check the log format selection.")
    if errors:
        st.code("\n".join(errors[:20]))
    st.stop()

if do_verify:
    with st.spinner("Verifying bots (reverse + forward DNS)…"):
        df = _verify(df)
else:
    df = bots.annotate(df)
    df["verification"] = df["verify_engine"].apply(lambda e: "not_checked" if e else "not_applicable")

if do_geo:
    with st.spinner("Geolocating IPs…"):
        df = _geo(df)

# ------------------------------------------------------------------ global filter
st.sidebar.divider()
st.sidebar.subheader("Filter (applies to all tabs)")

cat_options = ["All categories"] + sorted(df["category"].unique().tolist())
sel_cat = st.sidebar.selectbox(
    "Category", cat_options,
    help="e.g. 'AI Training', 'AI Search', 'Search Engine'. Groups bots by purpose.",
)
# Vendor + UA options narrow to the chosen category so the dropdowns stay relevant.
cat_df = df if sel_cat == "All categories" else df[df["category"] == sel_cat]
vendor_options = ["All vendors"] + sorted(cat_df["vendor"].unique().tolist())
sel_vendor = st.sidebar.selectbox("Vendor", vendor_options)
vend_df = cat_df if sel_vendor == "All vendors" else cat_df[cat_df["vendor"] == sel_vendor]
ua_options = ["All bots & users"] + sorted(vend_df["ua_label"].unique().tolist())
sel_ua = st.sidebar.selectbox("User agent / bot", ua_options)

bots_only = st.sidebar.checkbox("Bots only", value=False)
ver_present = [
    v for v in ["verified", "spoofed", "not_checked", "error", "not_applicable"]
    if v in set(df["verification"].unique())
]
sel_ver = st.sidebar.selectbox(
    "Verification status", ["All"] + ver_present,
    help="Enable 'Verify bots' above to populate verified/spoofed.",
)

parsed_total = n_lines - n_errors
df_all = df  # unfiltered (used for the full User Agents listing)

fdf = df
if sel_cat != "All categories":
    fdf = fdf[fdf["category"] == sel_cat]
if sel_vendor != "All vendors":
    fdf = fdf[fdf["vendor"] == sel_vendor]
if sel_ua != "All bots & users":
    fdf = fdf[fdf["ua_label"] == sel_ua]
if bots_only:
    fdf = fdf[fdf["is_bot"]]
if sel_ver != "All":
    fdf = fdf[fdf["verification"] == sel_ver]
df = fdf

st.title("SEO Log File Analyzer")

if df.empty:
    st.warning("No events match the current filter. Loosen the filter in the sidebar.")
    st.stop()

dmin = df["datetime"].min()
dmax = df["datetime"].max()
active_filters = []
if sel_cat != "All categories":
    active_filters.append(sel_cat)
if sel_vendor != "All vendors":
    active_filters.append(sel_vendor)
if sel_ua != "All bots & users":
    active_filters.append(sel_ua)
if bots_only:
    active_filters.append("bots only")
if sel_ver != "All":
    active_filters.append(f"verification={sel_ver}")
filter_note = f" · filter: {', '.join(active_filters)}" if active_filters else ""
st.caption(
    f"**{len(df):,}** events shown of **{parsed_total:,}** parsed · {n_errors:,} unparsed · "
    f"range {dmin:%Y-%m-%d} → {dmax:%Y-%m-%d} · UTC{filter_note}"
)

tabs = st.tabs(
    ["Overview", "URLs", "Response Codes", "User Agents", "Compare Bots", "Referers",
     "Directories", "IPs", "Countries", "Bytes", "Events"]
)

with tabs[0]:
    ov = metrics.overview(df)
    metric_grid(ov)
    st.divider()
    c1, c2 = st.columns([2, 1])
    with c1:
        st.plotly_chart(charts.response_codes_timeseries(metrics.events_timeseries(df)), use_container_width=True, key="ov_rc_ts")
    with c2:
        st.plotly_chart(charts.response_code_pie(df), use_container_width=True, key="ov_rc_pie")

with tabs[1]:
    st.subheader("URLs")
    url_table = metrics.by_url(df)
    st.dataframe(url_table, use_container_width=True, hide_index=True)
    st.download_button(
        "⬇ Download URL list (CSV)",
        url_table.to_csv(index=False).encode(),
        file_name="crawled_urls.csv",
        mime="text/csv",
        key="dl_urls",
    )

with tabs[2]:
    st.subheader("Response Codes")
    st.dataframe(metrics.by_response_code(df), use_container_width=True, hide_index=True)
    st.plotly_chart(charts.response_codes_timeseries(metrics.events_timeseries(df)), use_container_width=True, key="rc_ts")

with tabs[3]:
    st.subheader("User Agents")
    st.caption("Full breakdown (ignores the sidebar filter — use it to pick which bot to drill into).")
    st.dataframe(bots.user_agent_summary(df_all), use_container_width=True, hide_index=True)

    ai = bots.ai_bot_summary(df_all)
    if not ai.empty:
        st.subheader("AI Crawlers")
        st.caption("OpenAI, Anthropic, Perplexity, Google AI, Meta, and others — by vendor & category.")
        st.dataframe(ai, use_container_width=True, hide_index=True)

    vs = bots.verification_summary(df_all)
    if not vs.empty:
        st.subheader("Bot Verification")
        st.dataframe(vs, use_container_width=True, hide_index=True)
        if do_verify:
            st.plotly_chart(charts.bots_timeseries(df_all[df_all["is_bot"]]), use_container_width=True, key="ua_bots_ts")
    else:
        st.caption("Enable **Verify bots** in the sidebar for reverse-DNS verification status.")

with tabs[4]:
    st.subheader("Compare Bots")
    st.caption("Crawl footprint of each bot side by side (ignores the sidebar filter).")
    comp = metrics.bot_comparison(df_all)
    if comp.empty:
        st.info("No bot traffic detected in this log.")
    else:
        st.dataframe(comp, use_container_width=True, hide_index=True)
        st.download_button(
            "⬇ Download comparison (CSV)", comp.to_csv(index=False).encode(),
            file_name="bot_comparison.csv", mime="text/csv", key="dl_compare",
        )
        st.markdown("**Crawl coverage — top URLs × bot** (who hits what)")
        st.caption("Counts per URL per bot. Zeros reveal pages a bot never crawled.")
        matrix = metrics.url_bot_matrix(df_all, top_n=25)
        st.dataframe(matrix, use_container_width=True, hide_index=True)

with tabs[5]:
    st.subheader("Referers")
    st.dataframe(metrics.by_referer(df), use_container_width=True, hide_index=True)

with tabs[6]:
    st.subheader("Directories")
    st.dataframe(metrics.by_directory(df), use_container_width=True, hide_index=True)

with tabs[7]:
    st.subheader("IPs")
    st.dataframe(metrics.by_ip(df), use_container_width=True, hide_index=True)

with tabs[8]:
    st.subheader("Countries")
    if do_geo and "country" in df:
        cs = geo.country_summary(df)
        col1, col2 = st.columns([1, 2])
        with col1:
            st.dataframe(cs[["rank", "country", "num_events", "num_events_pct"]], use_container_width=True, hide_index=True)
        with col2:
            st.plotly_chart(charts.country_choropleth(cs), use_container_width=True, key="geo_map")
    else:
        st.info("Enable **Geolocate IPs** in the sidebar to populate this tab.")

with tabs[9]:
    st.subheader("Bytes")
    bytes_df = metrics.by_url(df).sort_values("total_bytes", ascending=False)
    st.dataframe(bytes_df, use_container_width=True, hide_index=True)

with tabs[10]:
    st.subheader("Events")
    st.plotly_chart(charts.events_timeseries(df), use_container_width=True, key="ev_ts")
    if do_verify or "is_bot" in df:
        st.plotly_chart(charts.bots_timeseries(df[df["is_bot"]]), use_container_width=True, key="ev_bots_ts")
