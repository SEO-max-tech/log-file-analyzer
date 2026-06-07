# SEO Log File Analyzer

A Screaming-Frog-style log file analyzer for SEO. Upload a server access log, get
crawl metrics: bot activity, response codes, crawled URLs, directories, bandwidth,
IPs, and geolocation — in an interactive Streamlit dashboard.

Built on **advertools** (parsing) + **pandas** (aggregation) + **Plotly** (charts) + **Streamlit** (UI).

![SEO Log File Analyzer dashboard](docs/screenshot.png)

## Features

| Tab | What it shows |
|-----|---------------|
| **Overview** | Unique URLs, events/day, bytes, CO2, avg response time, response-code split, time-series |
| **URLs** | Per-URL events, bytes, CO2, avg response time |
| **Response Codes** | Counts by status code + time-series by class (2xx/3xx/4xx/5xx) |
| **User Agents** | Per-UA crawl breakdown + **bot verification** (real Googlebot/Bingbot vs spoofers) |
| **Referers** | Referrer breakdown |
| **Directories** | Crawl rolled up by top-level path |
| **IPs** | Per-IP event + byte counts |
| **Countries** | IP → country geolocation + choropleth map |
| **Bytes** | Bandwidth per URL |
| **Events** | Daily events + per-bot activity over time |

### Bot verification
Confirms a self-declared Googlebot/Bingbot is genuine via the official method:
reverse-DNS the IP → hostname must end in `googlebot.com`/`google.com`/`search.msn.com`
→ forward-DNS that hostname must resolve back to the same IP. Spoofers are flagged.
Results cached in `.cache/bot_verify.json`.

### Geolocation
IP → country via the free [ip-api.com](http://ip-api.com) batch endpoint (no API key,
~15 batch requests/min). Cached in `.cache/geo.json`. Private IPs are skipped.

## Quick start

Clone, install, and run in one line:

```bash
git clone https://github.com/SEO-max-tech/log-file-analyzer.git && cd log-file-analyzer && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt && streamlit run app.py
```

The app opens at `http://localhost:8501` with the bundled sample log pre-loaded.

## Setup (step by step)

```bash
git clone https://github.com/SEO-max-tech/log-file-analyzer.git
cd log-file-analyzer
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

Then in the sidebar: upload an access log (or use the bundled sample), pick the log
format, and optionally enable bot verification / geolocation (slower — they hit the network).

## Log formats

Built-in: NCSA **common**, **combined**, **common_with_vhost**.
Extras: **combined_time** (combined + trailing response time in µs — the sample format),
**combined_msec** (combined + response time in ms).
Or pick **custom** and supply a named-group regex + field list (W3C, CDN logs, etc.).

## Sample data

```bash
python generate_sample_log.py --days 30 --events 4000 --out sample_logs/access.log
```

Generates a realistic combined+time log with Googlebot/Bingbot/human traffic, real
crawler IP ranges, varied response codes and byte sizes.

## Project layout

```
app.py                    Streamlit dashboard (all tabs)
analyzer/
  parser.py               advertools wrapper + normalization
  metrics.py              pandas aggregations
  bots.py                 UA classification + DNS bot verification
  geo.py                  IP → country (ip-api.com + cache)
  charts.py               Plotly figures
generate_sample_log.py    synthetic log generator
sample_logs/access.log    bundled sample
```
