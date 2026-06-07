"""Log parsing: wraps advertools.logs_to_df and normalizes the result.

Supports advertools' built-in formats (common, combined, common_with_vhost) plus
two extras commonly seen in SEO log analysis:

    combined_time  - NCSA combined + a trailing response-time field (microseconds)
    combined_msec  - NCSA combined + trailing response time in milliseconds

You can also pass a fully custom regex + field list for anything else (W3C, CDN logs).
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from urllib.parse import urlparse

import pandas as pd
from advertools import logs as advlogs
from advertools import logs_to_df

# advertools' combined regex, minus the trailing "$", so we can append fields.
_COMBINED_BODY = advlogs.LOG_FORMATS["combined"].rstrip("$").rstrip("\\s*")

CUSTOM_FORMATS = {
    "combined_time": {
        "regex": _COMBINED_BODY + r' (?P<time_taken>[0-9]+)\s*$',
        "fields": advlogs.LOG_FIELDS["combined"] + ["time_taken"],
        "time_unit": "us",  # microseconds
        "label": "Combined + response time (microseconds)",
    },
    "combined_msec": {
        "regex": _COMBINED_BODY + r' (?P<time_taken>[0-9]+)\s*$',
        "fields": advlogs.LOG_FIELDS["combined"] + ["time_taken"],
        "time_unit": "ms",
        "label": "Combined + response time (milliseconds)",
    },
}

BUILTIN_FORMATS = {
    "common": {"label": "NCSA Common", "time_unit": None},
    "combined": {"label": "NCSA Combined", "time_unit": None},
    "common_with_vhost": {"label": "Common + virtual host", "time_unit": None},
}

ALL_FORMATS = {**BUILTIN_FORMATS, **CUSTOM_FORMATS}


@dataclass
class ParseResult:
    df: pd.DataFrame
    errors: list[str] = field(default_factory=list)
    n_lines: int = 0
    n_errors: int = 0


def _time_to_ms(series: pd.Series, unit: str | None) -> pd.Series:
    vals = pd.to_numeric(series, errors="coerce")
    if unit == "us":
        return vals / 1000.0
    if unit == "ms":
        return vals
    return pd.Series([pd.NA] * len(series), index=series.index, dtype="float64")


def parse_log(
    log_file: str,
    fmt: str = "combined_time",
    custom_regex: str | None = None,
    custom_fields: list[str] | None = None,
    time_unit: str | None = None,
) -> ParseResult:
    """Parse a log file into a normalized DataFrame.

    fmt: one of ALL_FORMATS keys, or "custom" with custom_regex + custom_fields.
    """
    tmp_out = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False).name
    tmp_err = tempfile.NamedTemporaryFile(suffix=".txt", delete=False).name

    try:
        if fmt == "custom":
            if not custom_regex or not custom_fields:
                raise ValueError("custom format requires custom_regex and custom_fields")
            logs_to_df(log_file, tmp_out, tmp_err, log_format=custom_regex, fields=custom_fields)
            unit = time_unit
        elif fmt in CUSTOM_FORMATS:
            spec = CUSTOM_FORMATS[fmt]
            logs_to_df(log_file, tmp_out, tmp_err, log_format=spec["regex"], fields=spec["fields"])
            unit = spec["time_unit"]
        elif fmt in BUILTIN_FORMATS:
            logs_to_df(log_file, tmp_out, tmp_err, log_format=fmt)
            unit = None
        else:
            raise ValueError(f"unknown format: {fmt}")

        df = pd.read_parquet(tmp_out)
        errors: list[str] = []
        if os.path.exists(tmp_err):
            with open(tmp_err) as fh:
                errors = [ln.rstrip("\n") for ln in fh if ln.strip()]

        df = _normalize(df, unit)
        return ParseResult(df=df, errors=errors, n_lines=len(df) + len(errors), n_errors=len(errors))
    finally:
        for p in (tmp_out, tmp_err):
            try:
                os.unlink(p)
            except OSError:
                pass


def _normalize(df: pd.DataFrame, time_unit: str | None) -> pd.DataFrame:
    df = df.copy()

    # advertools prefixes columns; normalize common names.
    rename = {}
    for c in df.columns:
        lc = c.lower()
        if lc.endswith("client") or lc == "client":
            rename[c] = "client"
        elif lc.endswith("datetime") or lc == "datetime":
            rename[c] = "datetime"
        elif lc.endswith("request") or lc == "request":
            rename[c] = "request"
        elif lc.endswith("status") or lc == "status":
            rename[c] = "status"
        elif lc.endswith("size") or lc == "size":
            rename[c] = "size"
        elif lc.endswith("method") or lc == "method":
            rename[c] = "method"
        elif "referrer" in lc or "referer" in lc:
            rename[c] = "referer"
        elif "useragent" in lc or "user_agent" in lc:
            rename[c] = "user_agent"
        elif lc.endswith("time_taken") or lc == "time_taken":
            rename[c] = "time_taken"
    df = df.rename(columns=rename)

    # Datetime: advertools writes e.g. "01/Jan/2024:00:08:55 +0000".
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(
            df["datetime"], format="%d/%b/%Y:%H:%M:%S %z", errors="coerce", utc=True
        )
        df["date"] = df["datetime"].dt.date

    if "status" in df.columns:
        df["status"] = pd.to_numeric(df["status"], errors="coerce").astype("Int64")
        df["status_class"] = (df["status"] // 100).astype("Int64")

    if "size" in df.columns:
        df["size"] = pd.to_numeric(df["size"].replace("-", 0), errors="coerce").fillna(0).astype("int64")

    # URL path: request is the path already in combined; build a full url for display.
    if "request" in df.columns:
        df["url"] = df["request"].fillna("")
        df["path"] = df["url"].apply(lambda u: urlparse(u).path if u else "")
        df["directory"] = df["path"].apply(_top_directory)

    df["time_taken_ms"] = _time_to_ms(df["time_taken"], time_unit) if "time_taken" in df.columns else pd.NA

    return df


def _top_directory(path: str) -> str:
    if not path or path == "/":
        return "/"
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "/"
    # Group by first path segment; if it looks like a file, it lives at root.
    if "." in parts[0] and len(parts) == 1:
        return "/"
    return "/" + parts[0] + "/"
