"""User-agent classification + bot verification (reverse DNS + published IP ranges).

Search engines (Google, Bing, Apple, Amazon, CommonCrawl, Petal) are verified via
reverse + forward DNS. Most AI crawlers (OpenAI, Anthropic, Perplexity) do NOT support
reverse DNS — they publish IP CIDR ranges as JSON, so those are verified by checking
the source IP against the downloaded prefix list.

Verification statuses: verified / spoofed / error / not_applicable / not_checked.
Network results are cached in .cache/.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
_VERIFY_CACHE = os.path.join(_CACHE_DIR, "bot_verify.json")
_RANGES_CACHE = os.path.join(_CACHE_DIR, "ip_ranges.json")

# ----------------------------------------------------------------------- registry
# Ordered, specific patterns first. First regex match wins.
# verify: {"method": "rdns", "suffixes": (...)} | {"method": "cidr", "url": ...} | {"method": None}
_RDNS_GOOGLE = {"method": "rdns", "suffixes": (".googlebot.com", ".google.com", ".googleusercontent.com")}
_RDNS_BING = {"method": "rdns", "suffixes": (".search.msn.com",)}
_NONE = {"method": None}

BOT_REGISTRY = [
    # --- robots.txt-only tokens: should never appear as a real UA ---
    ("Google-Extended (anomaly)", r"Google-Extended", "Google", "Anomaly", _NONE),
    ("Applebot-Extended (anomaly)", r"Applebot-Extended", "Apple", "Anomaly", _NONE),

    # --- OpenAI ---
    ("ChatGPT-User", r"ChatGPT-User", "OpenAI", "AI Assistant (user)", {"method": "cidr", "url": "https://openai.com/chatgpt-user.json"}),
    ("OAI-SearchBot", r"OAI-SearchBot", "OpenAI", "AI Search", {"method": "cidr", "url": "https://openai.com/searchbot.json"}),
    ("OAI-AdsBot", r"OAI-AdsBot", "OpenAI", "AI Ads", {"method": "cidr", "url": "https://openai.com/adsbot.json"}),
    ("GPTBot", r"GPTBot", "OpenAI", "AI Training", {"method": "cidr", "url": "https://openai.com/gptbot.json"}),

    # --- Anthropic (3 active share one IP file) ---
    ("Claude-SearchBot", r"Claude-SearchBot", "Anthropic", "AI Search", {"method": "cidr", "url": "https://claude.com/crawling/bots.json"}),
    ("Claude-User", r"Claude-User", "Anthropic", "AI Assistant (user)", {"method": "cidr", "url": "https://claude.com/crawling/bots.json"}),
    ("ClaudeBot", r"ClaudeBot", "Anthropic", "AI Training", {"method": "cidr", "url": "https://claude.com/crawling/bots.json"}),
    ("Claude-Web (legacy)", r"Claude-Web|anthropic-ai", "Anthropic", "AI (legacy)", _NONE),

    # --- Perplexity ---
    ("Perplexity-User", r"Perplexity-User", "Perplexity", "AI Assistant (user)", {"method": "cidr", "url": "https://www.perplexity.ai/perplexity-user.json"}),
    ("PerplexityBot", r"PerplexityBot", "Perplexity", "AI Search", {"method": "cidr", "url": "https://www.perplexity.ai/perplexitybot.json"}),

    # --- Google AI / other Google crawlers (rDNS) ---
    ("Google-CloudVertexBot", r"Google-CloudVertexBot", "Google", "AI Training", _RDNS_GOOGLE),
    ("GoogleOther", r"GoogleOther", "Google", "Search Engine", _RDNS_GOOGLE),

    # --- Meta ---
    ("Meta-ExternalFetcher", r"meta-externalfetcher", "Meta", "AI Assistant (user)", _NONE),
    ("Meta-ExternalAgent", r"meta-externalagent", "Meta", "AI Training", _NONE),
    ("FacebookBot", r"FacebookBot", "Meta", "AI Training", _NONE),
    ("facebookexternalhit", r"facebookexternalhit", "Meta", "Social", _NONE),

    # --- other AI crawlers (UA-only, no public verification) ---
    ("Bytespider", r"Bytespider", "ByteDance", "AI Training", _NONE),
    ("cohere-training-data-crawler", r"cohere-training-data-crawler", "Cohere", "AI Training", _NONE),
    ("cohere-ai", r"cohere-ai", "Cohere", "AI Assistant (user)", _NONE),
    ("DuckAssistBot", r"DuckAssistBot", "DuckDuckGo", "AI Search", _NONE),
    ("MistralAI-User", r"MistralAI-User", "Mistral", "AI Assistant (user)", _NONE),
    ("DeepSeekBot", r"DeepSeekBot", "DeepSeek", "AI Training", _NONE),
    ("AI2Bot", r"AI2Bot|Ai2Bot", "Ai2", "AI Training", _NONE),
    ("YouBot", r"YouBot", "You.com", "AI Search", _NONE),
    ("Diffbot", r"Diffbot", "Diffbot", "AI Training", _NONE),
    ("Timpibot", r"Timpibot", "Timpi", "AI Training", _NONE),
    ("ImagesiftBot", r"ImagesiftBot", "ImageSift", "AI Training", _NONE),
    ("Omgili", r"omgili", "Webz.io", "AI Training", _NONE),

    # --- verifiable non-Google search/AI crawlers (rDNS) ---
    ("Applebot", r"Applebot", "Apple", "Search Engine", {"method": "rdns", "suffixes": (".applebot.apple.com",)}),
    ("Amazonbot", r"Amazonbot", "Amazon", "AI Search", {"method": "rdns", "suffixes": (".crawl.amazonbot.amazon",)}),
    ("CCBot", r"CCBot", "Common Crawl", "AI Training", {"method": "rdns", "suffixes": (".crawl.commoncrawl.org",)}),
    ("PetalBot", r"PetalBot|AspiegelBot", "Huawei", "Search Engine", {"method": "rdns", "suffixes": (".aspiegel.com", ".petalsearch.com")}),

    # --- classic search engines ---
    ("Googlebot Smartphone", r"Googlebot.*(Android|Mobile|iPhone)|(Android|Mobile|iPhone).*Googlebot", "Google", "Search Engine", _RDNS_GOOGLE),
    ("Googlebot Desktop", r"Googlebot", "Google", "Search Engine", _RDNS_GOOGLE),
    ("Bingbot Mobile", r"bingbot.*Mobile|Windows Phone.*bingbot", "Microsoft", "Search Engine", _RDNS_BING),
    ("Bingbot", r"bingbot|BingPreview", "Microsoft", "Search Engine", _RDNS_BING),
    ("YandexBot", r"YandexBot", "Yandex", "Search Engine", _NONE),
    ("Baiduspider", r"Baiduspider", "Baidu", "Search Engine", _NONE),
    ("DuckDuckBot", r"DuckDuckBot", "DuckDuckGo", "Search Engine", _NONE),

    # --- generic catch-all bot (keep last before humans) ---
    ("Other Bot", r"bot|crawler|spider|slurp|crawl", "Other", "Other Bot", _NONE),
]

# Compile once.
_COMPILED = [(label, re.compile(rx, re.I), vendor, cat, verify) for label, rx, vendor, cat, verify in BOT_REGISTRY]
_LABEL_TO_ENTRY = {label: (vendor, cat, verify) for label, _rx, vendor, cat, verify in _COMPILED}


def classify_ua_full(ua: str) -> tuple[str, bool, str, str]:
    """Return (label, is_bot, vendor, category)."""
    if not isinstance(ua, str) or not ua:
        return ("Unknown", False, "Unknown", "Unknown")
    for label, rx, vendor, cat, _verify in _COMPILED:
        if rx.search(ua):
            return (label, True, vendor, cat)
    if "iPhone" in ua or "Android" in ua:
        return ("Human Mobile", False, "Human", "Human")
    return ("Human Desktop", False, "Human", "Human")


def classify_ua(ua: str) -> tuple[str, bool]:
    """Backward-compatible: (label, is_bot)."""
    label, is_bot, _v, _c = classify_ua_full(ua)
    return (label, is_bot)


def verification_engine(label: str) -> str | None:
    """Return the verification method ('rdns'/'cidr') for a label, or None."""
    entry = _LABEL_TO_ENTRY.get(label)
    if not entry:
        return None
    return entry[2].get("method")


def annotate(df: pd.DataFrame) -> pd.DataFrame:
    """Add ua_label, is_bot, vendor, category columns."""
    df = df.copy()
    parts = df["user_agent"].apply(classify_ua_full)
    df["ua_label"] = parts.apply(lambda t: t[0])
    df["is_bot"] = parts.apply(lambda t: t[1])
    df["vendor"] = parts.apply(lambda t: t[2])
    df["category"] = parts.apply(lambda t: t[3])
    df["verify_engine"] = df["ua_label"].apply(verification_engine)
    return df


# ----------------------------------------------------------------------- caches
def _load_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except OSError:
        pass


# ----------------------------------------------------------------- rDNS verify
def _verify_rdns(ip: str, suffixes: tuple[str, ...], timeout: float = 3.0) -> str:
    socket.setdefaulttimeout(timeout)
    try:
        host = socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return "spoofed"
    if not any(host.endswith(s) for s in suffixes):
        return "spoofed"
    try:
        _, _, addrs = socket.gethostbyname_ex(host)
    except (socket.gaierror, OSError):
        return "error"
    return "verified" if ip in addrs else "spoofed"


# ----------------------------------------------------------------- CIDR verify
def _fetch_ranges(url: str, timeout: float = 15.0) -> list[str]:
    """Fetch a published IP-range JSON and return a list of CIDR strings.

    Handles the common schema {"prefixes": [{"ipv4Prefix": "..."} | {"ipv6Prefix": "..."}]}.
    """
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "seo-log-analyzer"})
        data = resp.json()
    except (requests.RequestException, ValueError):
        return []
    cidrs = []
    for entry in data.get("prefixes", []):
        cidr = entry.get("ipv4Prefix") or entry.get("ipv6Prefix")
        if cidr:
            cidrs.append(cidr)
    return cidrs


def _get_networks(urls: set[str], use_cache: bool = True) -> dict[str, list]:
    """Return {url: [ip_network, ...]} for each url, fetching+caching raw CIDR lists."""
    raw_cache = _load_json(_RANGES_CACHE) if use_cache else {}
    out: dict[str, list] = {}
    dirty = False
    for url in urls:
        cidrs = raw_cache.get(url)
        if cidrs is None:
            cidrs = _fetch_ranges(url)
            raw_cache[url] = cidrs
            dirty = True
        nets = []
        for c in cidrs:
            try:
                nets.append(ipaddress.ip_network(c, strict=False))
            except ValueError:
                continue
        out[url] = nets
    if dirty and use_cache:
        _save_json(_RANGES_CACHE, raw_cache)
    return out


def _ip_in_networks(ip: str, networks: list) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


# ----------------------------------------------------------------- orchestration
def verify_bots(df: pd.DataFrame, max_workers: int = 16, use_cache: bool = True) -> pd.DataFrame:
    """Annotate + verify. Adds 'verification' column.

    rDNS bots hit the network per unique IP. CIDR bots download each vendor's prefix
    JSON once, then check membership locally. All cached.
    """
    df = annotate(df)
    verdict_cache = _load_json(_VERIFY_CACHE) if use_cache else {}

    # Build per-row verification spec.
    specs = df["ua_label"].map(lambda lbl: _LABEL_TO_ENTRY.get(lbl, (None, None, _NONE))[2])

    # --- rDNS jobs ---
    rdns_rows = df[specs.apply(lambda s: s.get("method") == "rdns")]
    rdns_jobs = {}
    for ip, spec in zip(rdns_rows["client"], specs[rdns_rows.index]):
        suffixes = spec["suffixes"]
        key = f"rdns:{'|'.join(suffixes)}:{ip}"
        if key not in verdict_cache:
            rdns_jobs[key] = (ip, suffixes)
    if rdns_jobs:
        items = list(rdns_jobs.items())
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            results = list(ex.map(lambda kv: (kv[0], _verify_rdns(*kv[1])), items))
        verdict_cache.update(dict(results))

    # --- CIDR jobs ---
    cidr_rows = df[specs.apply(lambda s: s.get("method") == "cidr")]
    cidr_urls = {specs[i]["url"] for i in cidr_rows.index}
    networks = _get_networks(cidr_urls, use_cache=use_cache) if cidr_urls else {}
    for i in cidr_rows.index:
        url = specs[i]["url"]
        ip = df.at[i, "client"]
        key = f"cidr:{url}:{ip}"
        if key not in verdict_cache:
            nets = networks.get(url, [])
            if not nets:
                verdict_cache[key] = "error"  # couldn't load ranges
            else:
                verdict_cache[key] = "verified" if _ip_in_networks(ip, nets) else "spoofed"

    if use_cache:
        _save_json(_VERIFY_CACHE, verdict_cache)

    def lookup(row):
        spec = _LABEL_TO_ENTRY.get(row["ua_label"], (None, None, _NONE))[2]
        method = spec.get("method")
        ip = row["client"]
        if method == "rdns":
            return verdict_cache.get(f"rdns:{'|'.join(spec['suffixes'])}:{ip}", "error")
        if method == "cidr":
            return verdict_cache.get(f"cidr:{spec['url']}:{ip}", "error")
        return "not_applicable"

    df["verification"] = df.apply(lookup, axis=1)
    return df


# ----------------------------------------------------------------- summaries
def user_agent_summary(df: pd.DataFrame) -> pd.DataFrame:
    group_cols = ["user_agent", "ua_label"]
    if "vendor" in df:
        group_cols += ["vendor", "category"]
    g = df.groupby(group_cols).agg(
        unique_urls=("path", "nunique"),
        num_events=("path", "size"),
        total_bytes=("size", "sum"),
    ).reset_index()
    g["num_events_pct"] = round(100 * g["num_events"] / len(df), 3)
    g["average_bytes"] = (g["total_bytes"] / g["num_events"]).astype(int)
    return g.sort_values("num_events", ascending=False).reset_index(drop=True)


def verification_summary(df: pd.DataFrame) -> pd.DataFrame:
    verifiable = df[df["verify_engine"].notna()]
    if verifiable.empty:
        return pd.DataFrame()
    g = verifiable.groupby(["ua_label", "vendor", "verification"]).size().reset_index(name="num_events")
    return g.sort_values("num_events", ascending=False).reset_index(drop=True)


def ai_bot_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Breakdown of AI-category bots by vendor + category."""
    ai = df[df["category"].astype(str).str.startswith("AI")] if "category" in df else pd.DataFrame()
    if ai.empty:
        return pd.DataFrame()
    g = ai.groupby(["vendor", "category", "ua_label"]).agg(
        num_events=("path", "size"),
        unique_urls=("path", "nunique"),
        total_bytes=("size", "sum"),
    ).reset_index()
    g["num_events_pct"] = round(100 * g["num_events"] / len(df), 3)
    return g.sort_values("num_events", ascending=False).reset_index(drop=True)
