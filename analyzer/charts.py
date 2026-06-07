"""Plotly chart builders for the dashboard."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

_STATUS_COLORS = {
    "1xx": "#9e9e9e",
    "2xx": "#4caf50",
    "3xx": "#ffc107",
    "4xx": "#f44336",
    "5xx": "#8b0000",
}


def response_codes_timeseries(piv: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for col in piv.columns:
        if col == "date":
            continue
        fig.add_trace(
            go.Scatter(
                x=piv["date"], y=piv[col], mode="lines+markers", name=col,
                line=dict(color=_STATUS_COLORS.get(col)),
            )
        )
    fig.update_layout(title="Response Codes Over Time", xaxis_title="", yaxis_title="Events", height=380, legend_orientation="h")
    return fig


def events_timeseries(df: pd.DataFrame) -> go.Figure:
    daily = df.dropna(subset=["date"]).groupby("date").size().reset_index(name="events")
    fig = px.line(daily, x="date", y="events", markers=True, title="Events Per Day")
    fig.update_traces(line_color="#ff5722")
    fig.update_layout(height=380, xaxis_title="", yaxis_title="Events")
    return fig


def bots_timeseries(df: pd.DataFrame) -> go.Figure:
    t = df.dropna(subset=["date"])
    daily = t.groupby(["date", "ua_label"]).size().reset_index(name="events")
    fig = px.line(daily, x="date", y="events", color="ua_label", markers=True, title="Bot Activity Over Time")
    fig.update_layout(height=400, xaxis_title="", yaxis_title="Events", legend_orientation="h")
    return fig


def response_code_pie(df: pd.DataFrame) -> go.Figure:
    counts = df["status_class"].dropna().map(lambda c: f"{int(c)}xx").value_counts()
    fig = go.Figure(
        go.Pie(
            labels=counts.index.tolist(),
            values=counts.values.tolist(),
            marker_colors=[_STATUS_COLORS.get(l) for l in counts.index],
            hole=0.4,
        )
    )
    fig.update_layout(title="Response Code Share", height=380)
    return fig


def country_choropleth(summary: pd.DataFrame) -> go.Figure:
    # ip-api returns ISO-2 codes; plotly maps most reliably by country name.
    s = summary[~summary["country"].isin(["Unknown", "Private"])]
    fig = px.choropleth(
        s, locations="country", locationmode="country names",
        color="num_events", hover_name="country", color_continuous_scale="Greens",
    )
    fig.update_layout(title="Events by Country", height=420, geo=dict(showframe=False))
    return fig
