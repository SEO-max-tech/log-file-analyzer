"""Pandas aggregations that reproduce Screaming Frog Log Analyser tables."""
from __future__ import annotations

import pandas as pd

# CO2 model: Sustainable Web Design ~ 0.494 g per MB transferred (operational).
# SF reports mg; 0.494 g/MB = 0.494 mg/KB ... use grams/byte then to mg.
_CO2_MG_PER_BYTE = 0.494 / (1024 * 1024) * 1000  # mg per byte


def co2_mg(total_bytes: float) -> float:
    return total_bytes * _CO2_MG_PER_BYTE


def overview(df: pd.DataFrame) -> dict:
    n = len(df)
    days = df["date"].nunique() if "date" in df else 0
    total_bytes = int(df["size"].sum()) if "size" in df else 0
    status = df["status_class"]
    out = {
        "unique_urls": df["path"].nunique() if "path" in df else 0,
        "unique_urls_per_day": round((df["path"].nunique() / days), 2) if days else 0,
        "total_events": n,
        "events_per_day": round(n / days, 2) if days else 0,
        "total_bytes": total_bytes,
        "average_bytes": int(total_bytes / n) if n else 0,
        "bytes_per_day": round(total_bytes / days, 1) if days else 0,
        "total_co2_mg": round(co2_mg(total_bytes), 2),
        "days": days,
    }
    if "time_taken_ms" in df and df["time_taken_ms"].notna().any():
        out["average_time_taken_ms"] = int(df["time_taken_ms"].mean())
    else:
        out["average_time_taken_ms"] = None

    def cls_pct(c):
        cnt = int((status == c).sum())
        return cnt, round(100 * cnt / n, 2) if n else 0

    err = int(status.isin([4, 5]).sum())
    out["errors"] = err
    out["errors_pct"] = round(100 * err / n, 2) if n else 0
    for c, name in [(1, "provisional"), (2, "success"), (3, "redirection"), (4, "client_error"), (5, "server_error")]:
        cnt, pct = cls_pct(c)
        out[name] = cnt
        out[f"{name}_pct"] = pct
    return out


def _agg_bytes_co2(g: pd.DataFrame) -> dict:
    tb = int(g["size"].sum())
    return {
        "num_events": len(g),
        "total_bytes": tb,
        "average_bytes": int(g["size"].mean()) if len(g) else 0,
        "total_co2_mg": round(co2_mg(tb), 3),
    }


def by_url(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    n_total = len(df)
    days = df["date"].nunique() or 1
    for url, g in df.groupby("path"):
        d = _agg_bytes_co2(g)
        d["url"] = url
        d["num_events_pct"] = round(100 * d["num_events"] / n_total, 3)
        d["bytes_per_day"] = int(d["total_bytes"] / days)
        if g["time_taken_ms"].notna().any():
            d["avg_response_ms"] = int(g["time_taken_ms"].mean())
        rows.append(d)
    cols = ["url", "num_events", "num_events_pct", "total_bytes", "average_bytes", "bytes_per_day", "total_co2_mg"]
    out = pd.DataFrame(rows)
    if "avg_response_ms" in out:
        cols.append("avg_response_ms")
    return out[cols].sort_values("num_events", ascending=False).reset_index(drop=True)


def by_response_code(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("status").size().reset_index(name="num_events")
    g["num_events_pct"] = round(100 * g["num_events"] / len(df), 2)
    return g.sort_values("num_events", ascending=False).reset_index(drop=True)


def by_directory(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    n_total = len(df)
    for d, g in df.groupby("directory"):
        a = _agg_bytes_co2(g)
        a["directory"] = d
        a["num_events_pct"] = round(100 * a["num_events"] / n_total, 3)
        if g["time_taken_ms"].notna().any():
            a["avg_response_ms"] = int(g["time_taken_ms"].mean())
        rows.append(a)
    out = pd.DataFrame(rows)
    cols = ["directory", "num_events", "num_events_pct", "average_bytes", "total_bytes", "total_co2_mg"]
    if "avg_response_ms" in out:
        cols.append("avg_response_ms")
    return out[cols].sort_values("num_events", ascending=False).reset_index(drop=True)


def by_ip(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("client").agg(num_events=("client", "size"), total_bytes=("size", "sum")).reset_index()
    g["num_events_pct"] = round(100 * g["num_events"] / len(df), 3)
    g = g.rename(columns={"client": "ip"})
    return g.sort_values("num_events", ascending=False).reset_index(drop=True)


def by_referer(df: pd.DataFrame) -> pd.DataFrame:
    if "referer" not in df:
        return pd.DataFrame()
    g = df.assign(referer=df["referer"].replace("", "-")).groupby("referer").size().reset_index(name="num_events")
    g["num_events_pct"] = round(100 * g["num_events"] / len(df), 3)
    return g.sort_values("num_events", ascending=False).reset_index(drop=True)


def events_timeseries(df: pd.DataFrame, by: str = "status_class") -> pd.DataFrame:
    """Daily event counts, pivoted by status class (for the response-code chart)."""
    t = df.dropna(subset=["date"]).copy()
    if by == "status_class":
        piv = t.pivot_table(index="date", columns="status_class", aggfunc="size", fill_value=0)
        piv.columns = [f"{int(c)}xx" for c in piv.columns]
        return piv.reset_index()
    return t.groupby("date").size().reset_index(name="events")
