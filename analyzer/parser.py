"""Log parsing: normalizes many server log formats into one DataFrame schema.

Regex-based (via advertools):
    common, combined, common_with_vhost  - NCSA Apache/Nginx
    combined_time                         - combined + response time (microseconds)
    combined_msec                         - combined + response time (milliseconds)
    custom                                - your own named-group regex + field list

Non-regex parsers (built in here):
    json        - line-delimited JSON (Nginx json_log, Filebeat, k8s) via key aliases
    w3c         - IIS / W3C Extended (space-delimited, #Fields: header)
    cloudflare  - Cloudflare Logpush HTTP-requests JSON (PascalCase fields)

Every path produces canonical columns: client, datetime (UTC), method, url, status,
size, referer, user_agent, time_taken — then _finalize derives date, status_class,
path, directory, time_taken_ms.
"""
from __future__ import annotations

import json
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
        "time_unit": "us",
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

# Non-regex formats parsed by dedicated builders below.
NONREGEX_FORMATS = {
    "cloudflare": {"label": "Cloudflare Logpush (JSON)", "time_unit": "ms"},
    "json": {"label": "JSON (one object per line)", "time_unit": "s"},
    "w3c": {"label": "IIS / W3C Extended", "time_unit": "ms"},
}

ALL_FORMATS = {**BUILTIN_FORMATS, **CUSTOM_FORMATS, **NONREGEX_FORMATS}


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
    if unit == "s":
        return vals * 1000.0
    return pd.Series([pd.NA] * len(series), index=series.index, dtype="float64")


# --------------------------------------------------------------------- dispatch
def parse_log(
    log_file: str,
    fmt: str = "combined_time",
    custom_regex: str | None = None,
    custom_fields: list[str] | None = None,
    time_unit: str | None = None,
) -> ParseResult:
    """Parse a log file into a normalized DataFrame. fmt: an ALL_FORMATS key or 'custom'."""
    if fmt == "json":
        df, errors = _parse_json(log_file)
        df = _finalize(df, time_unit or "s")
        return ParseResult(df, errors, len(df) + len(errors), len(errors))
    if fmt == "cloudflare":
        df, errors = _parse_cloudflare(log_file)
        df = _finalize(df, "ms")
        return ParseResult(df, errors, len(df) + len(errors), len(errors))
    if fmt == "w3c":
        df, errors = _parse_w3c(log_file)
        df = _finalize(df, "ms")
        return ParseResult(df, errors, len(df) + len(errors), len(errors))

    # --- regex path (advertools) ---
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
        df = _normalize_regex(df)
        df = _finalize(df, unit)
        return ParseResult(df, errors, len(df) + len(errors), len(errors))
    finally:
        for p in (tmp_out, tmp_err):
            try:
                os.unlink(p)
            except OSError:
                pass


# --------------------------------------------------------------- regex rename
def _normalize_regex(df: pd.DataFrame) -> pd.DataFrame:
    """Map advertools' (sometimes prefixed) columns to canonical names + parse apache date."""
    df = df.copy()
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
    if "datetime" in df.columns:
        # advertools writes e.g. "01/Jan/2024:00:08:55 +0000".
        df["datetime"] = pd.to_datetime(
            df["datetime"], format="%d/%b/%Y:%H:%M:%S %z", errors="coerce", utc=True
        )
    return df


# ----------------------------------------------------------------- finalize
def _finalize(df: pd.DataFrame, time_unit: str | None) -> pd.DataFrame:
    """Derive date, status_class, size, url/path/directory, time_taken_ms from canonical cols."""
    df = df.copy()
    if df.empty:
        # Guarantee downstream columns exist even with zero rows.
        for c in ["client", "datetime", "url", "path", "directory", "status", "status_class",
                  "size", "referer", "user_agent", "time_taken_ms", "date"]:
            if c not in df.columns:
                df[c] = pd.Series(dtype="object")
        return df

    if "datetime" in df.columns:
        if not pd.api.types.is_datetime64_any_dtype(df["datetime"]):
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce", utc=True)
        df["date"] = df["datetime"].dt.date

    if "status" in df.columns:
        df["status"] = pd.to_numeric(df["status"], errors="coerce").astype("Int64")
        df["status_class"] = (df["status"] // 100).astype("Int64")

    if "size" in df.columns:
        df["size"] = pd.to_numeric(df["size"].replace("-", 0), errors="coerce").fillna(0).astype("int64")

    url_src = "url" if "url" in df.columns else ("request" if "request" in df.columns else None)
    if url_src:
        df["url"] = df[url_src].fillna("").astype(str)
        df["path"] = df["url"].apply(lambda u: urlparse(u).path if u else "")
        df["directory"] = df["path"].apply(_top_directory)

    for col in ("referer", "user_agent"):
        if col not in df.columns:
            df[col] = ""

    df["time_taken_ms"] = _time_to_ms(df["time_taken"], time_unit) if "time_taken" in df.columns else pd.NA
    return df


def _top_directory(path: str) -> str:
    if not path or path == "/":
        return "/"
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "/"
    if "." in parts[0] and len(parts) == 1:
        return "/"
    return "/" + parts[0] + "/"


def _split_request(val):
    """A request field may be a full 'GET /path HTTP/1.1' or just '/path'. Return the path part."""
    if not isinstance(val, str):
        return val
    parts = val.split(" ")
    if len(parts) == 3 and parts[0].isupper():
        return parts[1]
    return val


# ---------------------------------------------------------------- JSON parser
_JSON_ALIASES = {
    "client": ["remote_addr", "client_ip", "clientip", "ip", "c_ip", "x_forwarded_for", "host_ip"],
    "datetime": ["time", "timestamp", "time_local", "@timestamp", "time_iso8601", "date", "datetime"],
    "method": ["request_method", "method", "verb"],
    "url": ["request_uri", "uri", "request", "url", "path", "cs_uri_stem"],
    "status": ["status", "status_code", "response_status", "sc_status"],
    "size": ["body_bytes_sent", "bytes_sent", "bytes", "size", "response_size", "length"],
    "referer": ["http_referer", "referer", "referrer"],
    "user_agent": ["http_user_agent", "user_agent", "agent", "useragent", "ua"],
    "time_taken": ["request_time", "response_time", "duration", "time_taken", "upstream_response_time"],
}


def _parse_json(log_file: str) -> tuple[pd.DataFrame, list[str]]:
    rows, errors = [], []
    with open(log_file, encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                errors.append(ln)
    if not rows:
        return pd.DataFrame(), errors
    raw = pd.DataFrame(rows)
    lower = {c.lower(): c for c in raw.columns}
    out = pd.DataFrame(index=raw.index)
    for canon, cands in _JSON_ALIASES.items():
        for cand in cands:
            if cand in lower:
                out[canon] = raw[lower[cand]]
                break
    if "url" in out.columns:
        out["url"] = out["url"].apply(_split_request)
    return out, errors


# ------------------------------------------------------------ Cloudflare parser
def _cf_datetime(series: pd.Series) -> pd.Series:
    """EdgeStartTimestamp may be epoch ns (default), epoch s, ms, or RFC3339 string."""
    num = pd.to_numeric(series, errors="coerce")
    if num.notna().all():
        m = num.dropna().abs().median()
        if m > 1e17:
            unit = "ns"
        elif m > 1e14:
            unit = "us"
        elif m > 1e11:
            unit = "ms"
        else:
            unit = "s"
        return pd.to_datetime(num, unit=unit, utc=True, errors="coerce")
    return pd.to_datetime(series, utc=True, errors="coerce")


def _parse_cloudflare(log_file: str) -> tuple[pd.DataFrame, list[str]]:
    rows, errors = [], []
    with open(log_file, encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                errors.append(ln)
    if not rows:
        return pd.DataFrame(), errors
    raw = pd.DataFrame(rows)
    out = pd.DataFrame(index=raw.index)
    cf_map = {
        "client": "ClientIP",
        "method": "ClientRequestMethod",
        "status": "EdgeResponseStatus",
        "size": "EdgeResponseBytes",
        "referer": "ClientRequestReferer",
        "user_agent": "ClientRequestUserAgent",
        "host": "ClientRequestHost",
        "time_taken": "EdgeTimeToFirstByteMs",
    }
    for canon, cf in cf_map.items():
        if cf in raw.columns:
            out[canon] = raw[cf]
    # URL: prefer full URI (with query), fall back to path.
    if "ClientRequestURI" in raw.columns:
        out["url"] = raw["ClientRequestURI"]
    elif "ClientRequestPath" in raw.columns:
        out["url"] = raw["ClientRequestPath"]
    if "EdgeStartTimestamp" in raw.columns:
        out["datetime"] = _cf_datetime(raw["EdgeStartTimestamp"])
    return out, errors


# ------------------------------------------------------------- W3C / IIS parser
# Tolerant token matching — see research notes on cs(User-Agent)/cs(Referer) spellings.
_W3C_TOKENS = {
    "client": ["c-ip"],
    "method": ["cs-method"],
    "status": ["sc-status"],
    "size": ["sc-bytes"],
    "time_taken": ["time-taken"],
    "referer": ["cs(referer)", "cs(referrer)"],
    "user_agent": ["cs(user-agent)", "cs(useragent)"],
}


def _parse_w3c(log_file: str) -> tuple[pd.DataFrame, list[str]]:
    fields = None
    data_rows, errors = [], []
    with open(log_file, encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.rstrip("\n").rstrip("\r")
            if not ln:
                continue
            if ln.startswith("#"):
                if ln.lower().startswith("#fields:"):
                    fields = ln.split(":", 1)[1].strip().split()
                continue
            if fields is None:
                errors.append(ln)
                continue
            parts = ln.split(" ")
            if len(parts) != len(fields):
                errors.append(ln)
                continue
            data_rows.append(dict(zip(fields, parts)))
    if not data_rows:
        return pd.DataFrame(), errors
    raw = pd.DataFrame(data_rows)
    lower = {c.lower(): c for c in raw.columns}
    out = pd.DataFrame(index=raw.index)
    for canon, toks in _W3C_TOKENS.items():
        for tok in toks:
            if tok in lower:
                out[canon] = raw[lower[tok]]
                break
    # URL = uri-stem (+ query if present and not "-").
    stem = raw[lower["cs-uri-stem"]] if "cs-uri-stem" in lower else None
    if stem is not None:
        if "cs-uri-query" in lower:
            q = raw[lower["cs-uri-query"]].fillna("-")
            out["url"] = [s + ("?" + qq if qq and qq != "-" else "") for s, qq in zip(stem, q)]
        else:
            out["url"] = stem
    # datetime = date + time (UTC).
    if "date" in lower and "time" in lower:
        out["datetime"] = pd.to_datetime(
            raw[lower["date"]] + " " + raw[lower["time"]], utc=True, errors="coerce"
        )
    # IIS encodes spaces in UA/referer as '+'.
    for col in ("user_agent", "referer"):
        if col in out.columns:
            out[col] = out[col].astype(str).str.replace("+", " ", regex=False)
    return out, errors
