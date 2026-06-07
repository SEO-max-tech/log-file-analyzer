"""User-agent classification + search-engine bot verification (reverse+forward DNS).

Verification follows Google's / Bing's official guidance:
  1. Reverse-DNS the IP -> hostname.
  2. Hostname must end with an official crawler domain.
  3. Forward-DNS that hostname -> must resolve back to the original IP.
Spoofers fail step 2 or 3.
"""
from __future__ import annotations

import json
import os
import re
import socket
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

_CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache", "bot_verify.json")

# UA classification: (label, compiled regex, is_bot)
_UA_RULES = [
    ("Googlebot Smartphone", re.compile(r"Googlebot.*(Android|Mobile|iPhone)|(Android|Mobile|iPhone).*Googlebot", re.I), True),
    ("Googlebot Desktop", re.compile(r"Googlebot", re.I), True),
    ("Google Other", re.compile(r"Google-InspectionTool|Storebot-Google|GoogleOther|AdsBot-Google|Mediapartners-Google", re.I), True),
    ("Bingbot Mobile", re.compile(r"bingbot.*Mobile|Windows Phone.*bingbot", re.I), True),
    ("Bingbot", re.compile(r"bingbot|BingPreview", re.I), True),
    ("DuckDuckBot", re.compile(r"DuckDuckBot", re.I), True),
    ("YandexBot", re.compile(r"YandexBot", re.I), True),
    ("Baiduspider", re.compile(r"Baiduspider", re.I), True),
    ("Applebot", re.compile(r"Applebot", re.I), True),
    ("GPTBot", re.compile(r"GPTBot", re.I), True),
    ("ClaudeBot", re.compile(r"ClaudeBot|Claude-Web|anthropic", re.I), True),
    ("Other Bot", re.compile(r"bot|crawler|spider|slurp", re.I), True),
]

# Official crawler reverse-DNS domains -> the engine the IP must verify as.
_VERIFY_DOMAINS = {
    "Googlebot": (".googlebot.com", ".google.com"),
    "Bingbot": (".search.msn.com",),
}


def classify_ua(ua: str) -> tuple[str, bool]:
    """Return (label, is_bot). Humans get an OS/browser-ish label."""
    if not isinstance(ua, str) or not ua:
        return ("Unknown", False)
    for label, rx, is_bot in _UA_RULES:
        if rx.search(ua):
            return (label, is_bot)
    if "iPhone" in ua or "Android" in ua:
        return ("Human Mobile", False)
    return ("Human Desktop", False)


def verification_engine(label: str) -> str | None:
    """Which verification domain-set applies to this bot label, if any."""
    if "Googlebot" in label or "Google" in label:
        return "Googlebot"
    if "Bingbot" in label:
        return "Bingbot"
    return None


def _load_cache() -> dict:
    if os.path.exists(_CACHE_PATH):
        try:
            with open(_CACHE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    try:
        with open(_CACHE_PATH, "w") as f:
            json.dump(cache, f)
    except OSError:
        pass


def _verify_ip(ip: str, engine: str, timeout: float = 3.0) -> str:
    """Return 'verified', 'spoofed', or 'error'."""
    domains = _VERIFY_DOMAINS.get(engine)
    if not domains:
        return "error"
    socket.setdefaulttimeout(timeout)
    try:
        host = socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return "spoofed"
    if not any(host.endswith(d) for d in domains):
        return "spoofed"
    try:
        _, _, addrs = socket.gethostbyname_ex(host)
    except (socket.gaierror, OSError):
        return "error"
    return "verified" if ip in addrs else "spoofed"


def verify_bots(
    df: pd.DataFrame, max_workers: int = 16, use_cache: bool = True
) -> pd.DataFrame:
    """Add columns: ua_label, is_bot, verify_engine, verification.

    verification is one of: verified / spoofed / error / not_applicable.
    Only Googlebot/Bingbot rows hit the network; results cached by (ip, engine).
    """
    df = df.copy()
    labels = df["user_agent"].apply(classify_ua)
    df["ua_label"] = labels.apply(lambda t: t[0])
    df["is_bot"] = labels.apply(lambda t: t[1])
    df["verify_engine"] = df["ua_label"].apply(verification_engine)

    cache = _load_cache() if use_cache else {}
    # Unique (ip, engine) pairs that need a lookup.
    to_check = (
        df.loc[df["verify_engine"].notna(), ["client", "verify_engine"]]
        .drop_duplicates()
        .itertuples(index=False)
    )
    jobs = []
    for ip, engine in to_check:
        key = f"{engine}:{ip}"
        if key not in cache:
            jobs.append((key, ip, engine))

    if jobs:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            results = list(ex.map(lambda j: (j[0], _verify_ip(j[1], j[2])), jobs))
        for key, status in results:
            cache[key] = status
        if use_cache:
            _save_cache(cache)

    def lookup(row):
        engine = row["verify_engine"]
        if not engine:
            return "not_applicable"
        return cache.get(f"{engine}:{row['client']}", "error")

    df["verification"] = df.apply(lookup, axis=1)
    return df


def user_agent_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Per-UA-string table like SF's User Agents tab."""
    g = df.groupby(["user_agent", "ua_label"]).agg(
        unique_urls=("path", "nunique"),
        num_events=("path", "size"),
        total_bytes=("size", "sum"),
    ).reset_index()
    g["num_events_pct"] = round(100 * g["num_events"] / len(df), 3)
    g["average_bytes"] = (g["total_bytes"] / g["num_events"]).astype(int)
    return g.sort_values("num_events", ascending=False).reset_index(drop=True)


def verification_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Counts by bot label + verification status."""
    bots = df[df["verify_engine"].notna()]
    if bots.empty:
        return pd.DataFrame()
    g = bots.groupby(["ua_label", "verification"]).size().reset_index(name="num_events")
    return g.sort_values("num_events", ascending=False).reset_index(drop=True)
