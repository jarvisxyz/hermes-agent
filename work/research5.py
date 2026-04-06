import re
import urllib.request

def fetch(url, label, max_chars=8000):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
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

# New March 31 article about 2026 RAV4 PHEP pricing
fetch("https://www.erinparktoyota.com/en/news/view/2026-toyota-rav4-plug-in-hybrid-four-grades-now-available", "ErinPark 2026 RAV4 PHEP pricing")

# Look for iZEV rebate info from government
urls = [
    "https://www.nrcan.gc.ca/energy-efficiency/transportation-energy-efficiency/electric-and-alternative-fuel-vehicles/buying-electric-or-alternative-fuel-vehicle/built-zero-emission-vehicle-program/24615",
    "https://www.canada.ca/en/services/transport/road-safety/zero-emission-vehicles/zero-emission-vehicle-infrastructure-program.html",
]
for u in urls:
    fetch(u, f"Govt iZEV page")

# Look for RAV4 Prime specific pricing 
fetch("https://www.emptytank.ca/2026/01/15/2026-toyota-rav4-plug-in-hybrid-pricing-specs-canada/", "EmptyTank 2026 RAV4 PHEP")
fetch("https://driving.ca/auto-news/toyota-announces-2026-rav4-plug-in-hybrid-pricing-specs-canada", "Driving.ca 2026 RAV4 PHEP")
