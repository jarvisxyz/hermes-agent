import re
import urllib.request

def fetch(url, label, max_chars=8000):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    try:
        html = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='replace')
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        print(f"\n{'='*70}")
        print(f"PAGE: {label}")
        print(f"URL: {url[:100]}")
        print(f"{'='*70}")
        print(text[:max_chars])
        return text
    except Exception as e:
        print(f"  ERROR: {e}")
        return ""

# Fetch detailed pricing pages
fetch("https://www.erinparktoyota.com/en/news/view/complete-2026-toyota-rav4-pricing-breakdown-le-to-x", "ErinPark 2026 RAV4 full pricing", max_chars=6000)
fetch("https://www.miltonvw.com/new/2026-Volkswagen-Tiguan.html", "Milton VW 2026 Tiguan - look for prices", max_chars=6000)
fetch("https://www.caredge.com/cars/depreciate/volkswagen-tiguan", "CarEdge VW Tiguan depreciation")
fetch("https://www.caredge.com/cars/depreciate/toyota-rav4-prime", "CarEdge RAV4 Prime depreciation")
fetch("https://www.autopadre.com/volkswagen/tiguan/depreciation", "AutoPadre Tiguan depreciation")
fetch("https://iseecars.com/toyota-rav4-resale-value", "iSeeCars RAV4 resale value")
fetch("https://www.kbb.com/volkswagen/tiguan/2022/depreciation/", "KBB 2022 Tiguan depreciation")
fetch("https://www.cbc.ca/news/canada/toronto/ontario-used-car-tax-pst-2025", "Ontario used car PST", max_chars=6000)
