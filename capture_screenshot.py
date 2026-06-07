"""Capture an Overview screenshot of the running app for the README.

Usage: streamlit must be running on the given URL. Then:
    python capture_screenshot.py http://localhost:8523 docs/screenshot.png
"""
import sys

from playwright.sync_api import sync_playwright

url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8523"
out = sys.argv[2] if len(sys.argv) > 2 else "docs/screenshot.png"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
    page.goto(url, wait_until="networkidle")
    # Wait for Streamlit to finish rendering metrics + a plotly chart.
    page.wait_for_selector("text=Unique URLs", timeout=30000)
    page.wait_for_selector(".js-plotly-plot", timeout=30000)
    page.wait_for_timeout(2500)  # let chart animations settle
    page.screenshot(path=out, full_page=False)
    browser.close()
print(f"saved {out}")
