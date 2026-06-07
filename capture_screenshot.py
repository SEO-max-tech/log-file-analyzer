"""Capture dashboard screenshots for the README.

Streamlit must be running on the given URL. Tabs are client-side, so this clicks
the named tab before shooting.

Usage:
    python capture_screenshot.py <url> <out.png> [tab_name] [--full]

Examples:
    python capture_screenshot.py http://localhost:8523 docs/screenshot.png
    python capture_screenshot.py http://localhost:8523 docs/ai-crawlers.png "User Agents" --full
"""
import sys

from playwright.sync_api import sync_playwright

url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8523"
out = sys.argv[2] if len(sys.argv) > 2 else "docs/screenshot.png"
tab_name = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else None
full_page = "--full" in sys.argv

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
    page.goto(url, wait_until="networkidle")
    page.wait_for_selector("text=Unique URLs", timeout=30000)
    page.wait_for_selector(".js-plotly-plot", timeout=30000)

    if tab_name:
        page.get_by_role("tab", name=tab_name).click()
        page.wait_for_timeout(1500)

    page.wait_for_timeout(2000)  # let render settle
    page.screenshot(path=out, full_page=full_page)
    browser.close()
print(f"saved {out}")
