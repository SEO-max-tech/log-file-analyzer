"""IP -> country geolocation via the free ip-api.com batch endpoint, with caching.

Free tier: no key, batch up to 100 IPs/request, ~15 batch requests/minute.
Results cached to .cache/geo.json so repeat runs don't re-hit the API.
Private/reserved IPs are skipped (reported as 'Private').
"""
from __future__ import annotations

import ipaddress
import json
import os
import time

import pandas as pd
import requests

_CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache", "geo.json")
_BATCH_URL = "http://ip-api.com/batch"
_FIELDS = "status,country,countryCode,query"


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


def _is_public(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def lookup_countries(
    ips: list[str], use_cache: bool = True, progress=None
) -> dict[str, dict]:
    """Return {ip: {'country':..., 'country_code':...}} for the given IPs."""
    cache = _load_cache() if use_cache else {}
    result: dict[str, dict] = {}
    pending = []
    for ip in set(ips):
        if ip in cache:
            result[ip] = cache[ip]
        elif not _is_public(ip):
            result[ip] = {"country": "Private", "country_code": "--"}
            cache[ip] = result[ip]
        else:
            pending.append(ip)

    batches = [pending[i : i + 100] for i in range(0, len(pending), 100)]
    for bi, batch in enumerate(batches):
        if progress:
            progress(bi, len(batches))
        try:
            resp = requests.post(
                _BATCH_URL, params={"fields": _FIELDS}, json=batch, timeout=15
            )
            data = resp.json()
        except (requests.RequestException, ValueError):
            for ip in batch:
                result[ip] = {"country": "Unknown", "country_code": "--"}
            continue
        for entry in data:
            ip = entry.get("query")
            if entry.get("status") == "success":
                rec = {"country": entry.get("country", "Unknown"), "country_code": entry.get("countryCode", "--")}
            else:
                rec = {"country": "Unknown", "country_code": "--"}
            result[ip] = rec
            cache[ip] = rec
        # Respect rate limit (~15/min) between batches.
        if bi < len(batches) - 1:
            time.sleep(4)

    if use_cache:
        _save_cache(cache)
    return result


def add_countries(df: pd.DataFrame, use_cache: bool = True, progress=None) -> pd.DataFrame:
    df = df.copy()
    mapping = lookup_countries(df["client"].tolist(), use_cache=use_cache, progress=progress)
    df["country"] = df["client"].map(lambda ip: mapping.get(ip, {}).get("country", "Unknown"))
    df["country_code"] = df["client"].map(lambda ip: mapping.get(ip, {}).get("country_code", "--"))
    return df


def country_summary(df: pd.DataFrame) -> pd.DataFrame:
    if "country" not in df:
        return pd.DataFrame()
    g = df.groupby(["country", "country_code"]).size().reset_index(name="num_events")
    g["num_events_pct"] = round(100 * g["num_events"] / len(df), 3)
    g = g.sort_values("num_events", ascending=False).reset_index(drop=True)
    g["rank"] = g.index + 1
    return g
