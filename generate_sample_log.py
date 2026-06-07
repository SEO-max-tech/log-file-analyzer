"""Generate a realistic synthetic access log for development/testing.

Produces NCSA "combined" format with a trailing response-time field (microseconds),
a very common Apache/Nginx extension:

    host - - [time] "METHOD path HTTP/1.1" status bytes "referer" "ua" time_us

Run:
    python generate_sample_log.py --days 30 --events 4000 --out sample_logs/access.log
"""
from __future__ import annotations

import argparse
import datetime as dt
import random

# Real crawler IP ranges (prefixes) so reverse-DNS / verification has something to chew on.
GOOGLEBOT_IPS = ["66.249.66.", "66.249.65.", "66.249.64.", "66.249.79."]
BINGBOT_IPS = ["157.55.39.", "207.46.13.", "40.77.167.", "13.66.139."]
HUMAN_IP_BLOCKS = ["104.28.", "98.207.", "24.6.", "73.158.", "172.58.", "92.40."]

USER_AGENTS = {
    "Googlebot Desktop": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Googlebot Smartphone": (
        "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 "
        "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    ),
    "Bingbot": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Bingbot Mobile": (
        "Mozilla/5.0 (Windows Phone 8.1; ARM; Trident/7.0; Touch; rv:11.0; IEMobile/11.0; "
        "NOKIA; Lumia 530) like Gecko (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
    ),
    "Human Chrome": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Human Safari iPhone": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    # --- AI crawlers ---
    "GPTBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.3; +https://openai.com/gptbot",
    "ChatGPT-User": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot",
    "OAI-SearchBot": "Mozilla/5.0 (compatible; OAI-SearchBot/1.3; +https://openai.com/searchbot)",
    "ClaudeBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "Claude-User": "Mozilla/5.0 (compatible; Claude-User/1.0; +Claude-User@anthropic.com)",
    "PerplexityBot": "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
    "Bytespider": "Mozilla/5.0 (compatible; Bytespider; spider-feedback@bytedance.com)",
    "Meta-ExternalAgent": "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)",
    "Amazonbot": "Mozilla/5.0 (compatible; Amazonbot/0.1; +https://developer.amazon.com/support/amazonbot)",
    "CCBot": "CCBot/2.0 (https://commoncrawl.org/faq/)",
}

# (path, weight, typical_bytes, mostly_status)
URLS = [
    ("/", 70, 5500, 200),
    ("/used_to_work.php", 27, 6100, 404),
    ("/used_to_work", 26, 4600, 404),
    ("/slow_page.php", 23, 11048346, 200),
    ("/dowload/press.pdf", 22, 11048346, 200),
    ("/nav_left.png", 17, 1425, 200),
    ("/intro.swf", 17, 5472306, 200),
    ("/sitemap.xml", 17, 9562, 200),
    ("/inconsistent.html", 16, 3350, 200),
    ("/robots.txt", 16, 2045, 200),
    ("/js/bigfoot.js", 14, 3425, 200),
    ("/nav_right.png", 13, 1425, 200),
    ("/image.jpg", 12, 104564, 200),
    ("/cute_cat.gif", 12, 204527, 200),
    ("/blog/", 11, 9474, 200),
    ("/js/jquery.min.js", 9, 2045, 200),
    ("/blog/post-1", 8, 8800, 200),
    ("/blog/post-2", 7, 8800, 301),
    ("/old-page", 6, 0, 301),
    ("/missing", 5, 1200, 404),
    ("/api/data", 5, 3200, 500),
    ("/checkout", 4, 7000, 503),
    ("/js/app.js", 4, 4200, 200),
]

REFERERS = [
    "-",
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://example.com/",
    "https://t.co/abc123",
]


def weighted_choice(items):
    population = [i[0] for i in items]
    weights = [i[1] for i in items]
    return random.choices(population, weights=weights, k=1)[0]


def pick_ip(ua_label: str) -> str:
    if "Googlebot" in ua_label:
        return random.choice(GOOGLEBOT_IPS) + str(random.randint(1, 254))
    if "Bingbot" in ua_label:
        return random.choice(BINGBOT_IPS) + str(random.randint(1, 254))
    block = random.choice(HUMAN_IP_BLOCKS)
    rest = ".".join(str(random.randint(1, 254)) for _ in range(4 - block.count(".")))
    return block + rest


def jitter_status(base: int) -> int:
    # Occasionally deviate so charts have variety.
    if random.random() < 0.08:
        return random.choice([200, 301, 302, 304, 404, 500, 503])
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--events", type=int, default=4000)
    ap.add_argument("--out", default="sample_logs/access.log")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start", default="2024-01-01", help="start date YYYY-MM-DD")
    args = ap.parse_args()

    random.seed(args.seed)
    start = dt.datetime.strptime(args.start, "%Y-%m-%d")
    ua_labels = list(USER_AGENTS.keys())
    # Weights: traditional bots heaviest, humans next, AI crawlers lighter but present.
    ua_weights = {
        "Googlebot Desktop": 22, "Googlebot Smartphone": 16, "Bingbot": 14, "Bingbot Mobile": 10,
        "Human Chrome": 10, "Human Safari iPhone": 7,
        "GPTBot": 5, "ChatGPT-User": 2, "OAI-SearchBot": 2, "ClaudeBot": 4, "Claude-User": 2,
        "PerplexityBot": 3, "Bytespider": 3, "Meta-ExternalAgent": 2, "Amazonbot": 2, "CCBot": 2,
    }
    weights = [ua_weights.get(lbl, 1) for lbl in ua_labels]

    lines = []
    for _ in range(args.events):
        ua_label = random.choices(ua_labels, weights=weights, k=1)[0]
        ua = USER_AGENTS[ua_label]
        ip = pick_ip(ua_label)
        chosen_path = weighted_choice([(r[0], r[1]) for r in URLS])
        path, _w, base_bytes, base_status = next(row for row in URLS if row[0] == chosen_path)
        status = jitter_status(base_status)
        size = 0 if status in (301, 302, 304) else max(0, int(base_bytes * random.uniform(0.6, 1.4)))
        method = "GET"
        # Spread timestamps across the window with daily variation.
        day_offset = random.randint(0, args.days - 1)
        secs = random.randint(0, 86399)
        ts = start + dt.timedelta(days=day_offset, seconds=secs)
        tstr = ts.strftime("%d/%b/%Y:%H:%M:%S +0000")
        referer = random.choice(REFERERS)
        # Response time: slow_page + pdf are heavy.
        if path in ("/slow_page.php", "/dowload/press.pdf", "/intro.swf"):
            time_us = random.randint(1_500_000, 6_000_000)
        else:
            time_us = random.randint(40_000, 1_400_000)
        line = (
            f'{ip} - - [{tstr}] "{method} {path} HTTP/1.1" {status} {size} '
            f'"{referer}" "{ua}" {time_us}'
        )
        lines.append((ts, line))

    lines.sort(key=lambda x: x[0])
    with open(args.out, "w") as f:
        f.write("\n".join(l for _, l in lines) + "\n")

    print(f"Wrote {len(lines)} events to {args.out} over {args.days} days.")


if __name__ == "__main__":
    main()
