import re
import urllib.request
import json

def fetch_and_search(query, label):
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    try:
        html = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f"  ERROR fetching: {e}")
        return
    
    # Extract results
    results = []
    # Pattern 1: data-u attribute
    blocks = html.split('<div class="results_links_deep">')
    for block in blocks[1:]:
        url_match = re.search(r'data-u="([^"]*)"', block)
        title_match = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not snippet_match:
            snippet_match = re.search(r'class="result__snippet">(.*?)</(.+?)(?=<div|$)', block, re.DOTALL)
        
        u = urllib.parse.unquote(url_match.group(1)) if url_match else ''
        t = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()[:150] if title_match else ''
        s = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()[:250] if snippet_match else ''
        results.append((u, t, s))
    
    # Pattern 2: href extraction
    if not results:
        links = re.findall(r'class="result__a"[^>]*href="([^"]*)"', html)
        titles = re.findall(r'class="result__a"[^>]*>([^<]*)<', html)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
        if not snippets:
            snippets = re.findall(r'class="result__snippet">(.*?)(?=<br|<a|</div)', html, re.DOTALL)
        for i in range(min(len(links), len(titles))):
            t = titles[i]
            u = urllib.parse.unquote(links[i])
            s = re.sub(r'<[^>]+>', '', snippets[i]).strip()[:250] if i < len(snippets) else ''
            results.append((u, t, s))
    
    print(f"  Found {len(results)} results")
    for i, (u, t, s) in enumerate(results[:6]):
        print(f"  {i+1}. {t}")
        print(f"     {u[:120]}")
        print(f"     {s[:200]}")
        print()
    return results

searches = [
    ("2026 Toyota RAV4 Prime XSE MSRP price Canada", "2026 RAV4 Prime XSE MSRP"),
    ("2026 VW Tiguan Highline MSRP price Canada", "2026 VW Tiguan Highline MSRP"),
    ("2022 VW Tiguan used price Ontario Canada", "Used 2022 VW Tiguan Ontario"),
    ("VW Tiguan depreciation Canada resale value percentage", "VW Tiguan depreciation Canada"),
    ("Toyota RAV4 Prime depreciation resale value Canada percentage", "RAV4 Prime depreciation Canada"),
    ("car depreciation Canada 3 year 5 year 10 year percentage 2024 2025", "Car depreciation Canada percentages"),
    ("iZEV rebate PHEV plug-in hybrid Canada 2025 amount", "iZEV rebate PHEV Canada"),
    ("Ontario used car sales tax PST HST 2025", "Ontario used vehicle tax"),
]

all_data = {}
for query, label in searches:
    print(f"\n{'='*70}")
    print(f"QUERY: {label}")
    print(f"{'='*70}")
    results = fetch_and_search(query, label)
    all_data[label] = results

# Save results
with open('/Users/jarvis/.hermes/hermes-agent/work/research_results.json', 'w') as f:
    # convert to dict
    save_data = {k: [(u,t,s[:200]) for u,t,s in v] for k,v in all_data.items()}
    json.dump(save_data, f, indent=2)
print("\nDone. Results saved.")
