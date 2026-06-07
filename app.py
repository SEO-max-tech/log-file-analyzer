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
    lab = df["user_agent"].apply(bots.classify_ua)
    df["ua_label"] = lab.apply(lambda t: t[0])
    df["is_bot"] = lab.apply(lambda t: t[1])
    df["verify_engine"] = df["ua_label"].apply(bots.verification_engine)
    df["verification"] = df["verify_engine"].apply(lambda e: "not_checked" if e else "not_applicable")

if do_geo:
    with st.spinner("Geolocating IPs…"):
        df = _geo(df)

st.title("SEO Log File Analyzer")
dmin = df["datetime"].min()
dmax = df["datetime"].max()
st.caption(
    f"**{n_lines:,}** lines · **{n_errors:,}** unparsed · range "
    f"{dmin:%Y-%m-%d} → {dmax:%Y-%m-%d} · timezone UTC"
)

tabs = st.tabs(
    ["Overview", "URLs", "Response Codes", "User Agents", "Referers",
     "Directories", "IPs", "Countries", "Bytes", "Events"]
)

with tabs[0]:
    ov = metrics.overview(df)
    metric_grid(ov)
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(charts.response_codes_timeseries(metrics.events_timeseries(df)), use_container_width=True)
    with c2:
        st.plotly_chart(charts.response_code_pie(df), use_container_width=True)

with tabs[1]:
    st.subheader("URLs")
    st.dataframe(metrics.by_url(df), use_container_width=True, hide_index=True)

with tabs[2]:
    st.subheader("Response Codes")
    st.dataframe(metrics.by_response_code(df), use_container_width=True, hide_index=True)
    st.plotly_chart(charts.response_codes_timeseries(metrics.events_timeseries(df)), use_container_width=True)

with tabs[3]:
    st.subheader("User Agents")
    st.dataframe(bots.user_agent_summary(df), use_container_width=True, hide_index=True)
    vs = bots.verification_summary(df)
    if not vs.empty:
        st.subheader("Bot Verification")
        st.dataframe(vs, use_container_width=True, hide_index=True)
        if do_verify:
            st.plotly_chart(charts.bots_timeseries(df[df["is_bot"]]), use_container_width=True)
    else:
        st.caption("Enable **Verify bots** in the sidebar for reverse-DNS verification status.")

with tabs[4]:
    st.subheader("Referers")
    st.dataframe(metrics.by_referer(df), use_container_width=True, hide_index=True)

with tabs[5]:
    st.subheader("Directories")
    st.dataframe(metrics.by_directory(df), use_container_width=True, hide_index=True)

with tabs[6]:
    st.subheader("IPs")
    st.dataframe(metrics.by_ip(df), use_container_width=True, hide_index=True)

with tabs[7]:
    st.subheader("Countries")
    if do_geo and "country" in df:
        cs = geo.country_summary(df)
        col1, col2 = st.columns([1, 2])
        with col1:
            st.dataframe(cs[["rank", "country", "num_events", "num_events_pct"]], use_container_width=True, hide_index=True)
        with col2:
            st.plotly_chart(charts.country_choropleth(cs), use_container_width=True)
    else:
        st.info("Enable **Geolocate IPs** in the sidebar to populate this tab.")

with tabs[8]:
    st.subheader("Bytes")
    bytes_df = metrics.by_url(df).sort_values("total_bytes", ascending=False)
    st.dataframe(bytes_df, use_container_width=True, hide_index=True)

with tabs[9]:
    st.subheader("Events")
    st.plotly_chart(charts.events_timeseries(df), use_container_width=True)
    if do_verify or "is_bot" in df:
        st.plotly_chart(charts.bots_timeseries(df[df["is_bot"]]), use_container_width=True)
