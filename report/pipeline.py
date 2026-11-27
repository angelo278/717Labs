"""717Labs automated AI visibility report.

Triggered by GitHub Actions when someone requests a free report on 717labs.co.uk.
Steps: read the customer's website -> write 5 questions -> ask 5 AI platforms ->
score the answers -> pick competitors and 3 opportunities -> build the PDF -> email it.

Run locally without API keys:  MOCK=1 python3 pipeline.py request.json
"""
import base64, concurrent.futures as cf, datetime as dt, html as htmllib, json, os, re, sys, time, traceback
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import build_report  # noqa: E402

MOCK = os.environ.get("MOCK") == "1"
INFO = os.environ.get("REPORT_COPY_TO", "info@717labs.co.uk")
FROM = os.environ.get("REPORT_FROM", "717Labs <reports@717labs.co.uk>")
UA = "Mozilla/5.0 (compatible; 717LabsReportBot/1.0; +https://717labs.co.uk)"
PLATFORMS = ["ChatGPT", "Perplexity", "Gemini", "Google AI Overviews", "Claude"]
TIMEOUT = 120


def log(*a):
    print(f"[{dt.datetime.now(dt.timezone.utc):%H:%M:%S}]", *a, flush=True)


def env(name):
    v = os.environ.get(name, "").strip()
    if not v and not MOCK:
        raise RuntimeError(f"Missing secret: {name}")
    return v


# ---------------------------------------------------------------- AI platform calls
def first_working(models, call):
    """Try each model name until one works (model names change over time)."""
    last = None
    for m in models:
        try:
            return call(m)
        except requests.HTTPError as e:
            body = e.response.text[:300] if e.response is not None else ""
            last = e
            if e.response is not None and e.response.status_code in (400, 404) and re.search(r"model|not.?found|does not exist|unsupported", body, re.I):
                log("model not available, trying next:", m, body[:120])
                continue
            raise
    raise last


def ask_chatgpt(q, loc):
    key = env("OPENAI_API_KEY")
    models = [m for m in [os.environ.get("OPENAI_MODEL"), "gpt-5.5-mini", "gpt-5.1-mini", "gpt-5-mini", "gpt-5.5"] if m]

    def call(m):
        ul = {"type": "approximate", "country": loc["cc"]}
        if loc.get("city"):
            ul["city"] = loc["city"]
        r = requests.post("https://api.openai.com/v1/responses", timeout=TIMEOUT,
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": m, "input": q, "tools": [{"type": "web_search", "user_location": ul}]})
        r.raise_for_status()
        return responses_text(r.json())
    return first_working(models, call)


def responses_text(j):
    texts, cites = [], []
    for item in j.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    texts.append(c.get("text", ""))
                    for a in c.get("annotations", []) or []:
                        if a.get("url"):
                            cites.append(a["url"])
        if item.get("type") == "search_results":
            cites += [x.get("url", "") for x in item.get("results", [])]
    return "\n".join(texts).strip(), cites


def ask_perplexity(q, loc):
    key = env("PERPLEXITY_API_KEY")
    r = requests.post("https://api.perplexity.ai/v1/agent", timeout=TIMEOUT,
                      headers={"Authorization": f"Bearer {key}"},
                      json={"effort": os.environ.get("PERPLEXITY_EFFORT", "fast"), "input": q})
    r.raise_for_status()
    return responses_text(r.json())


def ask_gemini(q, loc):
    key = env("GEMINI_API_KEY")
    models = [m for m in [os.environ.get("GEMINI_MODEL"), "gemini-flash-latest", "gemini-3.6-flash", "gemini-3.5-flash"] if m]

    def call(m):
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                          params={"key": key}, timeout=TIMEOUT,
                          json={"contents": [{"role": "user", "parts": [{"text": q}]}], "tools": [{"google_search": {}}]})
        r.raise_for_status()
        j = r.json()
        cand = (j.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        cites = [c.get("web", {}).get("title", "") for c in cand.get("groundingMetadata", {}).get("groundingChunks", [])]
        return text.strip(), cites
    return first_working(models, call)


def ask_claude(q, loc):
    key = env("ANTHROPIC_API_KEY")
    models = [m for m in [os.environ.get("ANTHROPIC_MODEL"), "claude-haiku-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"] if m]

    def call(m):
        tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3,
                "user_location": {"type": "approximate", "country": loc["cc"], **({"city": loc["city"]} if loc.get("city") else {})}}
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=TIMEOUT,
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                          json={"model": m, "max_tokens": 1500, "tools": [tool], "messages": [{"role": "user", "content": q}]})
        r.raise_for_status()
        j = r.json()
        text, cites = [], []
        for b in j.get("content", []):
            if b.get("type") == "text":
                text.append(b.get("text", ""))
                for c in b.get("citations", []) or []:
                    if c.get("url"):
                        cites.append(c["url"])
        return "".join(text).strip(), cites
    return first_working(models, call)


def ask_google_aio(q, loc):
    """Google AI Overviews via SerpApi (free plan: 250 searches a month)."""
    key = env("SERPAPI_API_KEY")
    cc = loc["cc"].lower()
    gl = "uk" if cc == "gb" else cc
    domain = GOOGLE_DOMAINS.get(cc, "google.com")
    base = {"api_key": key, "hl": "en", "gl": gl, "google_domain": domain}
    params = {**base, "engine": "google", "q": q}
    if loc.get("country_name"):
        params["location"] = f'{loc["city"]}, {loc["country_name"]}' if loc.get("city") else loc["country_name"]
    r = requests.get("https://serpapi.com/search.json", timeout=TIMEOUT, params=params)
    if r.status_code == 400 and "location" in params:   # SerpApi did not recognise the place name: retry by country only
        params.pop("location")
        r = requests.get("https://serpapi.com/search.json", timeout=TIMEOUT, params=params)
    r.raise_for_status()
    aio = r.json().get("ai_overview") or {}
    if aio.get("page_token") and not aio.get("text_blocks"):
        r2 = requests.get("https://serpapi.com/search.json", timeout=TIMEOUT,
                          params={**base, "engine": "google_ai_overview", "page_token": aio["page_token"]})
        if r2.ok:
            aio = r2.json().get("ai_overview") or aio
    if not aio:
        return "", []
    parts, cites = [], []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("snippet", "title") and isinstance(v, str):
                    parts.append(v)
                elif k == "link" and isinstance(v, str):
                    cites.append(v)
                elif k not in ("thumbnail", "page_token", "serpapi_link"):
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk({"text_blocks": aio.get("text_blocks", []), "references": aio.get("references", [])})
    return "\n".join(dict.fromkeys(p.strip() for p in parts if p.strip())), cites


GOOGLE_DOMAINS = {"gb": "google.co.uk", "us": "google.com", "ca": "google.ca", "au": "google.com.au", "nz": "google.co.nz",
                  "ie": "google.ie", "za": "google.co.za", "ae": "google.ae", "in": "google.co.in", "sg": "google.com.sg",
                  "ng": "google.com.ng", "ke": "google.co.ke", "mw": "google.mw", "de": "google.de", "fr": "google.fr",
                  "nl": "google.nl", "es": "google.es", "it": "google.it", "sa": "google.com.sa", "qa": "google.com.qa",
                  "hk": "google.com.hk", "my": "google.com.my", "ph": "google.com.ph", "gh": "google.com.gh"}


# ---------------------------------------------------------------- DataForSEO (one account for all five platforms)
DFS = "https://api.dataforseo.com/v3/"
DFS_COST = []                      # running total of DataForSEO spend for this report (USD)
# Google/DataForSEO location codes for common countries; others fall back to the country name.
LOC_CODES = {"GB": 2826, "US": 2840, "CA": 2124, "AU": 2036, "NZ": 2554, "IE": 2372, "ZA": 2710, "AE": 2784, "IN": 2356,
             "SG": 2702, "NG": 2566, "KE": 2404, "DE": 2276, "FR": 2250, "NL": 2528, "ES": 2724, "IT": 2380, "SA": 2682,
             "QA": 2634, "HK": 2344, "MY": 2458, "PH": 2608, "GH": 2288, "MW": 2454}
CLAUDE_SEARCH_CC = set("AR AT AU BE BR CA CH CL CN DE DK ES FI FR GB HK ID IN IT JP KR MX MY NL NO NZ PH PL PT RU SA SE TR TW US ZA".split())


def dfs_on():
    return bool(os.environ.get("DATAFORSEO_LOGIN", "").strip())


def dfs(path, task, method="POST"):
    """Call DataForSEO and return the first result object (raises on any error)."""
    auth = (env("DATAFORSEO_LOGIN"), env("DATAFORSEO_PASSWORD"))
    if method == "GET":
        r = requests.get(DFS + path, auth=auth, timeout=150)
    else:
        r = requests.post(DFS + path, auth=auth, timeout=150, json=[task])
    r.raise_for_status()
    j = r.json()
    if j.get("status_code") != 20000:
        raise RuntimeError(f"DataForSEO {path}: {j.get('status_code')} {j.get('status_message')}")
    t = (j.get("tasks") or [{}])[0]
    DFS_COST.append(float(t.get("cost") or 0))
    if t.get("status_code") != 20000:
        raise RuntimeError(f"DataForSEO {path}: {t.get('status_code')} {t.get('status_message')}")
    return (t.get("result") or [None])[0] or {}


def dfs_loc(loc):
    code = LOC_CODES.get(loc["cc"])
    if code:
        return {"location_code": code}
    name = re.sub(r"(?i)^the\s+", "", loc.get("country_name") or "").strip()
    return {"location_name": name or "United States"}


def dfs_kw(q):
    return q.replace("%", "%25").replace("+", "%2B")


def scraped_text(res):
    """Answer text and cited links from an LLM Scraper result (ChatGPT / Gemini as the public sees them)."""
    text = res.get("markdown") or "\n".join(i.get("markdown") or "" for i in res.get("items") or [])
    cites = [x.get("url", "") for x in res.get("sources") or [] if x.get("url")]
    for it in res.get("items") or []:
        if it.get("type", "").endswith("local_businesses"):        # map-style business lists count as part of the answer
            for b in it.get("items") or []:
                text += f"\n{b.get('title', '')}: {b.get('description') or ''} {b.get('address') or ''}"
                if b.get("url"):
                    cites.append(b["url"])
    return text.strip(), cites


def response_text(res):
    """Answer text and citations from an LLM Responses result (Claude / Perplexity / fallbacks)."""
    texts, cites = [], []
    for it in res.get("items") or []:
        if it.get("type") != "message":
            continue
        for sec in it.get("sections") or []:
            if sec.get("type") == "text" and sec.get("text"):
                texts.append(sec["text"])
            cites += [a.get("url", "") for a in sec.get("annotations") or [] if a.get("url")]
    cites += [x.get("url", "") for x in res.get("search_results") or [] if x.get("url")]
    return "\n".join(texts).strip(), cites


_MODELS = {}


def dfs_models(platform):
    """List of model names DataForSEO currently offers for a platform (cached)."""
    if platform not in _MODELS:
        try:
            r = requests.get(f"{DFS}ai_optimization/{platform}/llm_responses/models", timeout=60,
                             auth=(env("DATAFORSEO_LOGIN"), env("DATAFORSEO_PASSWORD")))
            names = re.findall(r'"model_name"\s*:\s*"([^"]+)"', r.text)
        except Exception:
            names = []
        _MODELS[platform] = list(dict.fromkeys(names))
    return _MODELS[platform]


def dfs_respond(platform, q, loc, prefer, extra):
    """Ask a model through DataForSEO LLM Responses, trying preferred model names, then any listed one that matches."""
    tried, last = set(), None
    candidates = [m for m in [os.environ.get(f"DFS_{platform.upper()}_MODEL")] + prefer if m]
    for attempt in range(2):
        for m in candidates:
            if m in tried:
                continue
            tried.add(m)
            try:
                return response_text(dfs(f"ai_optimization/{platform}/llm_responses/live",
                                         {"user_prompt": q[:500], "model_name": m, "max_output_tokens": 1500, **extra}))
            except Exception as e:
                last = e
                if not re.search(r"model", str(e), re.I):
                    raise
                log(f"{platform}: model {m} not available")
        listed = dfs_models(platform)
        candidates = [m for m in listed if re.search(r"haiku|mini|flash|sonar", m) and "embed" not in m][:4] or listed[:3]
    raise last or RuntimeError(f"No working {platform} model")


def dfs_scrape(platform, q, loc):
    task = {"keyword": dfs_kw(q[:2000]), "language_code": "en", "force_web_search": True, **dfs_loc(loc)}
    return scraped_text(dfs(f"ai_optimization/{platform}/llm_scraper/live/advanced", task))


def dfs_chatgpt(q, loc):
    try:
        return dfs_scrape("chat_gpt", q, loc)
    except Exception as e:                     # e.g. a country the scraper does not cover: use the model with web search
        log("ChatGPT scraper unavailable, using LLM responses:", str(e)[:120])
        return dfs_respond("chat_gpt", q, loc, ["gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"],
                           {"web_search": True, "web_search_country_iso_code": loc["cc"]})


def dfs_gemini(q, loc):
    try:
        return dfs_scrape("gemini", q, loc)
    except Exception as e:
        log("Gemini scraper unavailable, using LLM responses:", str(e)[:120])
        return dfs_respond("gemini", q, loc, ["gemini-2.5-flash", "gemini-flash"], {"web_search": True})


def dfs_claude(q, loc):
    extra = {"web_search": True}
    if loc["cc"] in CLAUDE_SEARCH_CC:
        extra["web_search_country_iso_code"] = loc["cc"]
        if loc.get("city"):
            extra["web_search_city"] = loc["city"]
    return dfs_respond("claude", q, loc, ["claude-haiku-4-5", "claude-sonnet-4-5", "claude-haiku"], extra)


def dfs_perplexity(q, loc):
    return dfs_respond("perplexity", q, loc, ["sonar", "sonar-pro"],
                       {"web_search": True, "web_search_country_iso_code": loc["cc"]})


def dfs_google_aio(q, loc):
    res = dfs("serp/google/organic/live/advanced",
              {"keyword": dfs_kw(q[:700]), "language_code": "en", "device": "desktop", "load_async_ai_overview": True, **dfs_loc(loc)})
    parts, cites = [], []

    def walk(o):
        if isinstance(o, dict):
            if o.get("markdown"):
                parts.append(o["markdown"])
            elif o.get("text") and o.get("type", "").startswith("ai_overview") and "reference" not in o.get("type", ""):
                parts.append(o["text"])
            if o.get("type") == "ai_overview_reference" and o.get("url"):
                cites.append(o["url"])
                return
            for k, v in o.items():
                if k not in ("images", "markdown", "text"):
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for it in res.get("items") or []:
        if "ai_overview" in it.get("type", ""):
            walk(it.get("items") or [])
    return "\n".join(dict.fromkeys(x.strip() for x in parts if x.strip())), cites


def ai_demand(p, domain, loc):
    """AI search demand: how often AI answers cite this website, and how much AI searching there is for this trade.
    Uses DataForSEO's database of real AI answers (Google AI Overviews). Returns None if there is no useful data."""
    if not dfs_on() or os.environ.get("REPORT_DEMAND", "1") == "0":
        return None
    base = {"platform": "google", "language_code": "en", **dfs_loc(loc)}

    def total(target):
        res = dfs("ai_optimization/llm_mentions/aggregated_metrics/live", {**base, "target": target, "internal_list_limit": 5})
        g = ((res.get("total") or {}).get("platform") or [{}])[0] or {}
        return int(g.get("mentions") or 0), int(g.get("ai_search_volume") or 0)
    out = {"trade": p.get("trade_plural", ""), "country": re.sub(r"(?i)^the\s+", "", p.get("country_name", ""))}
    try:
        out["your_mentions"], out["your_volume"] = total([{"domain": domain, "search_scope": "any"}])
    except Exception as e:
        log("demand (domain) skipped:", str(e)[:150])
        out["your_mentions"] = None
    kw = (p.get("trade_keyword") or "").strip()
    if kw:
        try:
            out["industry_mentions"], out["industry_volume"] = total([{"keyword": kw[:250], "search_scope": "question", "match_type": "partial_match"}])
        except Exception as e:
            log("demand (industry) skipped:", str(e)[:150])
    if not out.get("industry_volume") and not out.get("your_mentions"):
        return None
    out["keyword"] = kw
    return out


DFS_ASKERS = {"ChatGPT": dfs_chatgpt, "Perplexity": dfs_perplexity, "Gemini": dfs_gemini,
              "Google AI Overviews": dfs_google_aio, "Claude": dfs_claude}

ASKERS = {"ChatGPT": ask_chatgpt, "Perplexity": ask_perplexity, "Gemini": ask_gemini,
          "Google AI Overviews": ask_google_aio, "Claude": ask_claude}


# ---------------------------------------------------------------- the "brain" (judging and writing)
def llm_json(instructions, content, schema_hint):
    """Ask OpenAI for a JSON answer. Used for planning, judging and writing."""
    if MOCK:
        return mock_llm(instructions, content)
    key = env("OPENAI_API_KEY")
    models = [m for m in [os.environ.get("JUDGE_MODEL"), "gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini", "gpt-5.5-mini", "gpt-5.1-mini"] if m]

    def call(m):
        r = requests.post("https://api.openai.com/v1/responses", timeout=TIMEOUT,
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": m, "instructions": instructions + "\nReply with JSON only, matching: " + schema_hint,
                                "input": "Reply in JSON.\n" + content, "text": {"format": {"type": "json_object"}}})
        if not r.ok:
            log("OpenAI said:", r.status_code, r.text[:400].replace("\n", " "))
        r.raise_for_status()
        t, _ = responses_text(r.json())
        return json.loads(re.search(r"\{.*\}", t, re.S).group(0))
    for attempt in range(3):
        try:
            return first_working(models, call)
        except (ValueError, AttributeError, requests.RequestException) as e:
            log("llm_json retry", attempt, e)
            time.sleep(2 + attempt * 3)
    raise RuntimeError("The writing step failed three times")


# ---------------------------------------------------------------- website check
def fetch(url, limit=400_000):
    r = requests.get(url, timeout=20, headers={"User-Agent": UA}, allow_redirects=True)
    r.raise_for_status()
    return r.url, r.text[:limit]


def visible_text(h):
    h = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", htmllib.unescape(h)).strip()


def audit_site(website):
    url = website if re.match(r"https?://", website, re.I) else "https://" + website
    a = {"url": url, "reachable": False}
    if MOCK:
        a.update(reachable=True, final_url="https://harbourlanedental.co.uk/", https=True, title="Harbour Lane Dental | Bristol",
                 meta_description="", h1=["Welcome"], structured_data_types=[], homepage_words=310, has_uk_postcode=False,
                 has_phone=True, mentions_faq=False, mentions_reviews=True, ai_crawlers_blocked=["GPTBot"], blocks_everyone=False,
                 has_llms_txt=False, pages_checked=3)
        return a, "Harbour Lane Dental, friendly dentists in Bristol. Check-ups, hygiene, Invisalign."
    try:
        final, h = fetch(url)
    except Exception as e:
        a["error"] = f"Could not load the website ({type(e).__name__})"
        return a, ""
    a.update(reachable=True, final_url=final, https=final.startswith("https://"))
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", h)
    a["title"] = visible_text(m.group(1)) if m else ""
    m = re.search(r'(?is)<meta[^>]+name=["\']description["\'][^>]*content=["\'](.*?)["\']', h)
    a["meta_description"] = htmllib.unescape(m.group(1)).strip() if m else ""
    a["h1"] = [visible_text(x) for x in re.findall(r"(?is)<h1[^>]*>(.*?)</h1>", h)][:3]
    types = set()
    for blk in re.findall(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', h):
        types.update(re.findall(r'"@type"\s*:\s*"([^"]+)"', blk))
    a["structured_data_types"] = sorted(types)
    text = visible_text(h)
    a["homepage_words"] = len(text.split())
    a["has_uk_postcode"] = bool(re.search(r"\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b", text))
    a["has_phone"] = bool(re.search(r"(\+44\s?|0)\d[\d\s]{8,12}", text))
    a["mentions_faq"] = bool(re.search(r"\bFAQs?\b|frequently asked", text, re.I))
    a["mentions_reviews"] = bool(re.search(r"\breviews?\b|testimonials?|trustpilot", text, re.I))
    links = re.findall(r'(?is)<a[^>]+href=["\']([^"\'#]+)["\']', h)
    host = urlparse(final).netloc
    internal = []
    for l in links:
        u = urljoin(final, l)
        if urlparse(u).netloc == host and re.search(r"about|service|contact|pricing|price|faq", u, re.I) and u not in internal:
            internal.append(u)
    pages_text = [text[:6000]]
    for u in internal[:3]:
        try:
            pages_text.append(visible_text(fetch(u)[1])[:3000])
        except Exception:
            pass
    a["pages_checked"] = 1 + len(pages_text) - 1
    try:
        rob = requests.get(urljoin(final, "/robots.txt"), timeout=10, headers={"User-Agent": UA})
        rt = rob.text if rob.ok else ""
    except Exception:
        rt = ""
    blocked = []
    for bot in ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "ClaudeBot", "Claude-SearchBot", "Google-Extended", "Bingbot"]:
        if re.search(rf"(?is)user-agent:\s*{re.escape(bot)}\s*\n(?:[^\n]*\n)*?\s*disallow:\s*/\s*(\n|$)", rt):
            blocked.append(bot)
    a["ai_crawlers_blocked"] = blocked
    a["blocks_everyone"] = bool(re.search(r"(?is)user-agent:\s*\*\s*\n(?:allow:[^\n]*\n)*\s*disallow:\s*/\s*(\n|$)", rt))
    try:
        a["has_llms_txt"] = requests.get(urljoin(final, "/llms.txt"), timeout=10, headers={"User-Agent": UA}).status_code == 200
    except Exception:
        a["has_llms_txt"] = False
    return a, "\n\n".join(pages_text)


# ---------------------------------------------------------------- the report
def plan(req, audit, site_text):
    hint = '{"business_name":str,"name_variants":[str],"city":str,"country_code":str,"country_name":str,"trade_plural":str,"trade_keyword":str,"one_line_summary":str,"questions":[str x4]}'
    instr = ("You prepare an AI visibility check for a business, which may be in any country. From the website text, identify the business name "
             "(and short variants people might use), the town or city it serves (empty if unclear), the country it mainly serves "
             "(country_code = ISO 3166-1 alpha-2, e.g. GB, US, AE, ZA, AU; use the website's address, phone code, currency or domain; "
             "country_name in English), and what it does in plain words "
             "(trade_plural, e.g. 'accountants', 'wedding photographers'; trade_keyword = the single most common word or two-word phrase people use for this kind of business, singular and lower case, e.g. 'accountant', 'wedding photographer'). Write exactly 4 questions a potential customer would "
             "type into an AI assistant when looking for this kind of business. Do NOT include the business name in these 4. "
             "Include the town/city (and the country if the city alone is ambiguous) when known. Keep each under 20 words, "
             "in natural English as people in that country would phrase it. "
             "If the customer supplied example searches, base 1-2 questions on them.")
    content = json.dumps({"website": audit.get("final_url", req["website"]), "title": audit.get("title"),
                          "meta_description": audit.get("meta_description"), "h1": audit.get("h1"),
                          "customer_example_searches": req.get("searches", ""), "website_text": site_text[:9000]})
    p = llm_json(instr, content, hint)
    qs = [q.strip() for q in p.get("questions", []) if q.strip()][:4]
    if len(qs) < 4 or not p.get("business_name"):
        raise RuntimeError("Could not work out the business or questions from the website")
    city = p.get("city", "").strip()
    cc = str(p.get("country_code") or "").strip().upper()
    p["country_code"] = cc if re.fullmatch(r"[A-Z]{2}", cc) else "GB"
    p["country_name"] = str(p.get("country_name") or "").strip() or ("the United Kingdom" if p["country_code"] == "GB" else p["country_code"])
    qs.append(f"What do you know about {p['business_name']}{' in ' + city if city else ''}?")
    p["questions"] = qs
    return p


def ask_all(questions, loc):
    jobs = {}
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for qi, q in enumerate(questions):
            for p in PLATFORMS:
                fn = mock_answer if MOCK else (DFS_ASKERS if dfs_on() else ASKERS)[p]
                jobs[ex.submit(safe_ask, fn, p, q, loc)] = (qi, p)
        out = {}
        for f in cf.as_completed(jobs):
            out[jobs[f]] = f.result()
    return out


def safe_ask(fn, platform, q, loc):
    for attempt in range(2):
        try:
            if fn is mock_answer:
                return {"ok": True, **dict(zip(("text", "cites"), fn(platform, q, loc)))}
            t, c = fn(q, loc)
            return {"ok": True, "text": t, "cites": c}
        except Exception as e:
            log(f"{platform} failed ({attempt + 1}):", str(e)[:200])
            time.sleep(3)
    return {"ok": False, "text": "", "cites": []}


def clean(t):
    t = re.sub(r"\[(web:)?\d+\]|【[^】]*】", "", t)
    t = re.sub(r"\*\*|__|^#+\s*", "", t, flags=re.M)
    t = re.sub(r"\(\s*\[?[^)\s]*https?://[^)]*\)", "", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def judge(p, questions, answers, audit):
    variants = list(dict.fromkeys([p["business_name"]] + p.get("name_variants", [])))
    domain = urlparse(audit.get("final_url", audit["url"])).netloc.replace("www.", "")
    distinct = [w for w in re.findall(r"[A-Za-z0-9&']{4,}", p["business_name"])
                if w.lower() not in {"limited", "the", "and", "group", "services", "company", "agency", "studio", "solutions", "consulting"}]

    def one(key):
        qi, plat = key
        a = answers[key]
        if not a["ok"]:
            return key, None
        text = a["text"]
        if not text:
            return key, {"status": 0, "competitors": [], "note": "No AI answer was shown for this question."}
        is_brand = qi == len(questions) - 1
        hint = '{"status":0|1|2,"competitors":[{"name":str,"rank":int}],"note":str}'
        instr = ("You score one AI assistant answer for a visibility report. "
                 + ("This answer was asked about the business by name. status 2 = it describes the business correctly; "
                    "1 = it knows of it but is vague or has errors; 0 = it does not know it or confuses it with another business. "
                    "competitors must be []."
                    if is_brand else
                    "status 2 = the target business is named as one of the top 3 options or clearly recommended; 1 = it is mentioned "
                    "but not as a top choice; 0 = not mentioned. competitors = every OTHER real business the answer names as an option, "
                    "in order, with rank = its position (1 = first). Do not include platforms, directories or review sites "
                    "(e.g. Google, Yell, Trustpilot, Checkatrade) as competitors.")
                 + " note = one short plain-English sentence saying what the answer did, for the business owner.")
        content = json.dumps({"target_business": variants, "target_domain": domain, "question": questions[qi],
                              "facts_from_its_website": p.get("one_line_summary", ""),
                              "answer": text[:6000], "sources_cited": a["cites"][:15]})
        j = llm_json(instr, content, hint)
        st = int(j.get("status", 0)) if str(j.get("status", "0")).isdigit() else 0
        # Safety check: never give credit unless the name or website actually appears.
        blob = (text + " " + " ".join(a["cites"])).lower()
        if st > 0 and not (domain.lower() in blob or any(v.lower() in blob for v in variants)
                           or any(w.lower() in blob for w in distinct)):
            st = 0
        comps = [] if is_brand else [c for c in j.get("competitors", []) if isinstance(c, dict) and c.get("name")]
        return key, {"status": max(0, min(2, st)), "competitors": comps, "note": str(j.get("note", ""))[:220]}

    out = {}
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        for key, res in ex.map(one, list(answers)):
            out[key] = res
    return out


def norm(n):
    n = re.sub(r"(?i)\b(ltd|limited|llp|plc|& co|and co)\b\.?", "", n)
    return re.sub(r"[^a-z0-9]+", " ", n.lower()).strip()


def build_data(req, p, audit, answers, verdicts):
    questions = p["questions"]
    plats = [pl for pl in PLATFORMS if any(verdicts.get((qi, pl)) for qi in range(len(questions)))]
    if len(plats) < 3:
        raise RuntimeError(f"Only {len(plats)} AI platforms answered; not enough for a fair report")
    results = [[(verdicts.get((qi, pl)) or {"status": 0})["status"] for pl in plats] for qi in range(len(questions))]
    nrec = len(questions) - 1
    # competitors across the customer-style questions
    tally = {}
    for qi in range(nrec):
        for pl in plats:
            v = verdicts.get((qi, pl))
            if not v:
                continue
            seen = set()
            for c in v["competitors"]:
                k = norm(c["name"])
                if not k or k in seen or any(k == norm(x) for x in [p["business_name"]] + p.get("name_variants", [])):
                    continue
                seen.add(k)
                t = tally.setdefault(k, {"name": c["name"].strip(), "pts": 0, "appearances": 0})
                t["appearances"] += 1
                t["pts"] += 2 if int(c.get("rank", 9) or 9) <= 3 else 1
    max_rec = 2 * nrec * len(plats)
    comps = sorted(tally.values(), key=lambda t: (-t["pts"], -t["appearances"]))[:3]
    competitors = [{"name": c["name"], "score": round(c["pts"] / max_rec * 100), "appearances": c["appearances"]} for c in comps]
    rec_score = round(sum(sum(r) for r in results[:nrec]) / max_rec * 100)
    total_score = round(sum(map(sum, results)) / (2 * len(questions) * len(plats)) * 100)
    rec_mentions = sum(1 for r in results[:nrec] for v in r if v > 0)
    plats_found = sum(1 for j in range(len(plats)) if any(results[i][j] > 0 for i in range(nrec)))

    # two example answers: one where a competitor is named and the business is not, one where it appears
    def card(qi, pl):
        a, v = answers[(qi, pl)], verdicts[(qi, pl)]
        lines = [l.strip(" -•*") for l in clean(a["text"]).split("\n") if l.strip()]
        out, n = [], 0
        for l in lines:
            if n + len(l) > 520:
                out.append("…")
                break
            out.append(l)
            n += len(l)
        return {"platform": pl, "question": questions[qi], "answer": out or ["(No answer text)"], "note": v["note"], "status": v["status"]}
    missing = [(qi, pl) for qi in range(nrec) for pl in plats
               if verdicts.get((qi, pl)) and verdicts[(qi, pl)]["status"] == 0 and verdicts[(qi, pl)]["competitors"]]
    present = [(qi, pl) for qi in range(nrec) for pl in plats if verdicts.get((qi, pl)) and verdicts[(qi, pl)]["status"] > 0]
    picks = (missing[:1] + present[:1] + missing[1:2] + missing[2:3])[:2]
    screenshots = [card(*k) for k in picks]

    facts = {"business": p["business_name"], "city": p.get("city"), "country": p.get("country_name"), "trade": p.get("trade_plural"),
             "overall_score": total_score, "recommendation_score": rec_score,
             "platforms_checked": plats, "platforms_mentioning_in_customer_questions": plats_found,
             "customer_question_answers_including_business": f"{rec_mentions} of {nrec * len(plats)}",
             "top_choice_count": sum(1 for r in results[:nrec] for v in r if v == 2),
             "about_business_answers": {pl: results[-1][j] for j, pl in enumerate(plats)},
             "answer_notes": {f"Q{qi + 1} {pl}": (verdicts.get((qi, pl)) or {}).get("note") for qi in range(len(questions)) for pl in plats},
             "competitors": competitors, "website_check": {k: v for k, v in audit.items() if k not in ("url",)}}
    hint = ('{"summary":[str,str,str],"opportunities":[{"title":str,"found":str,"action":str,"why":str,"effort":"Low"|"Medium"|"High"} x3]}')
    instr = ("You write a free AI visibility report for a small business owner (in any country), in calm, plain British English. No jargon, "
             "no hype, no guarantees, no exclamation marks. Use ONLY the facts provided: never invent numbers, reviews, listings, "
             "awards or details you were not given. If you are not sure something is true, do not say it. "
             "summary: 3 sentences (each under 40 words) on what was found, using the provided numbers exactly. "
             "opportunities: exactly 3, the changes most likely to help AI assistants find, understand and trust this business. "
             "Base 'found' on the website check and answer notes. Good themes when supported by the facts: unblocking AI crawlers; "
             "clear service pages that answer customers' questions; FAQs; business name, address and phone shown clearly and "
             "consistently; structured data (schema) for the business; complete Google Business Profile and Bing Places listings; "
             "more independent reviews and mentions on trusted local or industry sites. Titles under 8 words. "
             "found/action/why: each 1-2 sentences, under 45 words.")
    w = llm_json(instr, json.dumps(facts), hint)
    opps = [o for o in w.get("opportunities", []) if isinstance(o, dict) and o.get("title")][:3]
    summary = [s for s in w.get("summary", []) if isinstance(s, str)][:3]
    if len(opps) < 3 or len(summary) < 2:
        raise RuntimeError("The writing step returned an incomplete report")
    for o in opps:
        o["effort"] = o.get("effort") if o.get("effort") in ("Low", "Medium", "High") else "Medium"

    return {
        "sample": False, "business": p["business_name"],
        "website": urlparse(audit.get("final_url", audit["url"])).netloc.replace("www.", ""),
        "location": (f'{p["city"]}, {p["country_name"]}' if p.get("city") else p.get("country_name")) or "your market", "country": p.get("country_name", ""), "date": dt.date.today().strftime("%-d %B %Y"),
        "platforms": plats, "questions": questions, "results": results, "summary": summary,
        "competitors": competitors, "screenshots": screenshots, "opportunities": opps,
        "rec_score": rec_score, "captured": True,
    }


# ---------------------------------------------------------------- email
def send_email(to, subject, html, pdf_path=None, bcc=True):
    if MOCK:
        log("MOCK email to", to, "|", subject, "| attachment:", pdf_path)
        return
    body = {"from": FROM, "to": [to], "subject": subject, "html": html, "reply_to": INFO}
    if bcc and to.lower() != INFO.lower():
        body["bcc"] = [INFO]
    if pdf_path:
        body["attachments"] = [{"filename": Path(pdf_path).name, "content": base64.b64encode(Path(pdf_path).read_bytes()).decode()}]
    r = requests.post("https://api.resend.com/emails", timeout=60, json=body,
                      headers={"Authorization": f"Bearer {env('RESEND_API_KEY')}"})
    if not r.ok:
        raise RuntimeError(f"Email failed: {r.status_code} {r.text[:300]}")


def email_html(paragraphs):
    ps = "".join(f'<p style="margin:0 0 14px">{p}</p>' for p in paragraphs)
    return (f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:#0F1B2D;max-width:560px">{ps}'
            '<p style="margin:22px 0 0;color:#5B6B82;font-size:13px">717Labs · <a href="https://717labs.co.uk" style="color:#2F6FEB">717labs.co.uk</a>'
            ' · Be seen where it matters most.</p></div>')


def first_name(n):
    n = (n or "").strip().split()
    return htmllib.escape(n[0].capitalize()) if n else "there"


# ---------------------------------------------------------------- main
def run(req):
    t0 = time.time()
    mode = req.get("mode", "report")
    name, email = first_name(req.get("name")), req["email"].strip()
    if mode == "repeat":
        send_email(email, "Your AI visibility report", email_html([
            f"Hi {name},", "Thank you for your request. We sent a free AI visibility report to this email address within the last 30 days, "
            "so we have not run a new one yet.", "If you would like another copy, or a fresh check, simply reply to this email."]))
        return "repeat"
    if mode == "over-cap":
        send_email(email, "Your AI visibility report is on its way", email_html([
            f"Hi {name},", "Thank you for your request. We are handling a high number of requests today, so your free AI visibility "
            "report will follow within 24 hours."]))
        send_email(INFO, f"[717Labs] Daily report limit reached: {req.get('website')}", email_html([
            f"A report request arrived after today's limit: {htmllib.escape(req.get('website', ''))} ({htmllib.escape(email)}).",
            "To send it, open GitHub, go to Actions, choose 'Free AI report', click 'Run workflow' and enter their details."]), bcc=False)
        return "over-cap"

    audit, site_text = audit_site(req["website"])
    if not audit["reachable"]:
        raise RuntimeError(audit.get("error", "Website could not be loaded"))
    log("website checked:", audit.get("final_url"))
    p = plan(req, audit, site_text)
    log("business:", p["business_name"], "|", p.get("city"), p.get("country_code"), "| questions:", p["questions"])
    loc = {"cc": p["country_code"], "city": p.get("city", ""), "country_name": p.get("country_name", "")}
    answers = ask_all(p["questions"], loc)
    log("answers collected:", sum(a["ok"] for a in answers.values()), "of", len(answers))
    verdicts = judge(p, p["questions"], answers, audit)
    data = build_data(req, p, audit, answers, verdicts)
    data["demand"] = ai_demand(p, data["website"], loc) if not MOCK else {
        "trade": "dentists", "country": "United Kingdom", "keyword": "dentist", "your_mentions": 3, "your_volume": 1900,
        "industry_mentions": 48210, "industry_volume": 2650000}
    data["capture"] = "dfs" if (dfs_on() or MOCK) else "api"
    log(f"DataForSEO spend for this report: ${sum(DFS_COST):.3f}")
    safe = re.sub(r"[^A-Za-z0-9]+", "-", data["business"]).strip("-")[:40] or "report"
    out = HERE / "out" / f"717Labs-AI-visibility-report-{safe}.pdf"
    out.parent.mkdir(exist_ok=True)
    (out.with_suffix(".json")).write_text(json.dumps({"request": {k: v for k, v in req.items() if k != "email"}, "data": data,
                                                      "answers": {f"{k[0]}|{k[1]}": v for k, v in answers.items()}, "dataforseo_cost_usd": round(sum(DFS_COST), 4)}, indent=2))
    build_report.render_pdf(data, str(out))
    log("PDF built:", out.name, "score", build_report.build(data)[1])
    send_email(email, f"Your free AI visibility report: {data['business']}", email_html([
        f"Hi {name},",
        f"Thank you for requesting a free AI visibility report for <b>{htmllib.escape(data['business'])}</b>. It is attached as a PDF.",
        "It shows whether ChatGPT, Perplexity, Gemini, Claude and Google's AI Overviews mention your business when customers ask "
        "for what you offer, which businesses appear instead, and the three changes we believe would make the biggest difference.",
        "If you would like to talk it through, just reply to this email and we will arrange a short call. There is no obligation.",
        "Best wishes,<br>The 717Labs team"]), pdf_path=out)
    log(f"done in {time.time() - t0:.0f}s")
    return "sent"


def main():
    req = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else json.loads(os.environ["REQUEST_JSON"])
    try:
        try:
            run(req)
        except Exception:
            log("first attempt failed:\n" + traceback.format_exc())
            time.sleep(10)
            run(req)
    except Exception:
        err = traceback.format_exc()
        log("report failed:\n" + err)
        try:
            send_email(req["email"], "Your AI visibility report is on its way", email_html([
                f"Hi {first_name(req.get('name'))},", "Thank you for your request. Your free AI visibility report is taking a little "
                "longer than usual to prepare. It will follow within 24 hours."]))
            send_email(INFO, f"[717Labs] Report failed: {req.get('website')}", email_html([
                f"The automatic report for {htmllib.escape(req.get('website', ''))} ({htmllib.escape(req.get('email', ''))}) failed twice. "
                "The customer has been told it will follow within 24 hours.",
                "To retry: GitHub → Actions → 'Free AI report' → 'Run workflow'.",
                "<pre style='font-size:11px;white-space:pre-wrap'>" + htmllib.escape(err[-2500:]) + "</pre>"]), bcc=False)
        except Exception:
            log("could not send failure emails:\n" + traceback.format_exc())
        sys.exit(1)


# ---------------------------------------------------------------- test doubles (MOCK=1)
def mock_answer(platform, q, loc):
    if "What do you know" in q:
        return ("Harbour Lane Dental is a dental practice in Bristol offering check-ups, hygiene and Invisalign. "
                "Some listings show an older phone number.", ["https://harbourlanedental.co.uk"])
    pool = ["Clifton Smile Studio", "Redland Dental Care", "Bristol Family Dentists", "Harbour Lane Dental"]
    k = (len(platform) + len(q)) % 4
    names = pool[k:] + pool[:k]
    if platform in ("ChatGPT", "Claude"):
        names = [n for n in names if n != "Harbour Lane Dental"]
    return ("Here are some well-reviewed options:\n" + "\n".join(f"{i + 1}. {n}: friendly team and good reviews." for i, n in enumerate(names[:3])), [])


def mock_llm(instructions, content):
    c = json.loads(content)
    if "score one AI assistant answer" not in instructions and "summary:" not in instructions:
        return {"business_name": "Harbour Lane Dental", "name_variants": ["Harbour Lane"], "city": "Bristol", "country_code": "GB", "country_name": "United Kingdom",
                "trade_plural": "dentists", "one_line_summary": "Dental practice in Bristol",
                "questions": ["Who are the best dentists in Bristol?", "Can you recommend an NHS dentist in Bristol?",
                              "Which dentists in Bristol offer Invisalign?", "I'm nervous about the dentist. Who should I see in Bristol?"]}
    if "score one AI assistant answer" in instructions:
        a = c["answer"]
        if "asked about the business by name" in instructions:
            return {"status": 1, "competitors": [], "note": "It knew the practice but gave an out-of-date phone number."}
        names = re.findall(r"\d\. ([^:]+):", a)
        st = 0
        for i, n in enumerate(names):
            if n == "Harbour Lane Dental":
                st = 2 if i < 3 else 1
        return {"status": st, "competitors": [{"name": n, "rank": i + 1} for i, n in enumerate(names) if n != "Harbour Lane Dental"],
                "note": "Recommended the practice." if st == 2 else ("Mentioned the practice." if st else "Did not mention the practice.")}
    return {"summary": ["Harbour Lane Dental appears in some AI answers but is rarely the first choice.",
                        "ChatGPT and Claude did not mention the practice in any customer question.",
                        "Clifton Smile Studio is named most often instead."],
            "opportunities": [{"title": "Make your services clearer", "found": "f", "action": "a", "why": "w", "effort": "Low"},
                              {"title": "Show your details consistently", "found": "f", "action": "a", "why": "w", "effort": "Low"},
                              {"title": "Build more independent reviews", "found": "f", "action": "a", "why": "w", "effort": "Medium"}]}


if __name__ == "__main__":
    main()
