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

# Full article about 2026 RAV4 PHEP pricing
full_url = "https://www.erinparktoyota.com/en/news/view/2026-toyota-rav4-plug-in-hybrid-four-grades-now-available-starting-at-48750"
fetch(full_url, "ErinPark 2026 RAV4 PHEP full article")

# Also try emptytank
fetch("https://emptytank.ca/2026/03/", "EmptyTank March 2026")

# Toyota Canada official page
fetch("https://www.toyota.ca/toyota/en/vehicles/rav4-phev", "Toyota.ca RAV4 PHEP")
