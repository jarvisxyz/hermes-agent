import re
import urllib.request

def fetch_url(url, label):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    try:
        html = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='replace')
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()
        print(f"\n{'='*70}")
        print(f"PAGE: {label}")
        print(f"URL: {url}")
        print(f"{'='*70}")
        # Print relevant sections (first 3000 chars of text)
        print(text[:3000])
        return text
    except Exception as e:
        print(f"  ERROR: {e}")
        return ""

def search_ddg(query, label):
    import urllib.parse
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    try:
        html = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='replace')
        results = []
        links = re.findall(r'class="result__a"[^>]*href="([^"]*)"', html)
        titles = re.findall(r'class="result__a"[^>]*>([^<]*)<', html)
        snippets = re.findall(r'class="result__snippet">(.*?)(?=<br|<a|</div)', html, re.DOTALL)
        for i in range(min(len(links), len(titles))):
            u = urllib.parse.unquote(links[i]).split('//duckduckgo.com')[0]
            t = titles[i]
            s = re.sub(r'<[^>]+>', '', snippets[i]).strip()[:250] if i < len(snippets) else ''
            results.append((u, t, s))
        
        if not results:
            blocks = html.split('data-u="')
            for block in blocks[1:]:
                url_end = block.split('"')[0]
                title_m = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
                snip_m = re.search(r'class="result__snippet">(.*?)</a>', block, re.DOTALL)
                u = 'https:' + url_end if url_end.startswith('//') else url_end
                # decode the ddg redirect
                if u.startswith('https:////duckduckgo.com'):
                    real_url = re.search(r'uddg=([^&]*?)&', url_end)
                    if real_url:
                        u = urllib.parse.unquote(real_url.group(1))
                t = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()[:150] if title_m else ''
                s = re.sub(r'<[^>]+>', '', snip_m.group(1)).strip()[:250] if snip_m else ''
                results.append((u, t, s))
        
        print(f"\n{'='*70}")
        print(f"SEARCH: {label}")
        print(f"{'='*70}")
        print(f"Found {len(results)} results")
        for u, t, s in results[:8]:
            print(f"\n  {t}")
            print(f"  {u[:120]}")
            print(f"  {s[:200]}")
        return results
    except Exception as e:
        print(f"  ERROR: {e}")
        return []

# Fetch key pages
fetch_url("https://emptytank.ca/2026/01/06/here-are-the-canadian-prices-of-2026-toyota-rav4/", "EmptyTank 2026 RAV4 pricing")
fetch_url("https://driving.ca/auto-news/news/2026-toyota-rav4-hybrid-canada-price", "Driving.ca 2026 RAV4 pricing")
fetch_url("https://www.erinparktoyota.com/en/news/view/complete-2026-toyota-rav4-pricing-breakdown-le-to-x", "ErinPark Toyota 2026 RAV4 pricing")
fetch_url("https://www.miltonvw.com/new/2026-Volkswagen-Tiguan.html", "Milton VW 2026 Tiguan pricing")
fetch_url("https://www.vmrcanada.com/used-car/values/2022-volkswagen-tiguan.html", "VMR Canada 2022 Tiguan used values")
fetch_url("https://www.cargurus.ca/Cars/l-Used-2022-Volkswagen-Tiguan-Ontario-c30681_L412505", "CarGurus 2022 Tiguan Ontario")

# More specific searches
search_ddg("2026 Toyota RAV4 Prime XSE AWD MSRP Canadian price $", "RAV4 Prime XSE specific price")
search_ddg("2026 VW Tiguan Highline Turbo R-Line MSRP Canada freight PDI price", "VW Tiguan Highline MSRP")
search_ddg("Canada iZEV rebate plug-in hybrid PHEV 2025 2026 $5000 $2500", "iZEV rebate amount 2025")
search_ddg("Ontario used car PST retail sales tax RST buyer", "Ontario used car PST 13%")
search_ddg("2022 Volkswagen Tiguan used price average Ontario Canada km $24000 $26000", "Used 2022 Tiguan average price")
search_ddg("Volkswagen Tiguan depreciation 3 year 5 year value retained Canada", "Tiguan depreciation rate Canada")
search_ddg("Toyota RAV4 Prime resale value retention depreciation Canada 2024 2025", "RAV4 Prime resale value Canada")
search_ddg("Canadian used car depreciation rate year 1 2 3 5 compact SUV average percentage", "Canadian SUV depreciation percentages")
