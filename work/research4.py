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

# VW of Newmarket for Highline pricing
fetch("https://www.vwofnewmarket.com/new/2026-Volkswagen-Tiguan-Highline_Turbo_R_Line.html", "VW Newmarket - Highline pricing")

# Toyota media release for 2026 RAV4
fetch("https://media.toyota.ca/en/releases/2026/the-canadian-built-rav4-is-all-new-for-2026--and-offer", "Toyota Media Release 2026 RAV4 Canada")

# Motor Illustrated for 2026 RAV4 pricing
fetch("https://motorillustrated.com/toyota-debuts-all-hybrid-2026-rav4-in-canada-built-locally-and-pri", "Motor Illustrated 2026 RAV4 pricing")

# iZEV rebate info
fetch("https://www.nrcan.gc.ca/energy-efficiency/transportation-energy-efficiency/electric-and-alternative-fuel-vehicles/buying-electric-or-alternative-fuel-vehicle/built-zero-emission-vehicle-program/24615", "NRCan iZEV rebate PHEV")

# KBB RAV4 Prime depreciation
fetch("https://www.kbb.com/toyota/rav4-prime/2024/depreciation/", "KBB 2024 RAV4 Prime depreciation")

# CarEdge VW Tiguan depreciation
fetch("https://www.caredge.com/cars/depreciate/volkswagen-tiguan", "CarEdge VW Tiguan depreciation")
