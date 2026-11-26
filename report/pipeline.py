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


def ask_chatgpt(q, city):
    key = env("OPENAI_API_KEY")
    models = [m for m in [os.environ.get("OPENAI_MODEL"), "gpt-5.5-mini", "gpt-5.1-mini", "gpt-5-mini", "gpt-5.5"] if m]

    def call(m):
        loc = {"type": "approximate", "country": "GB"}
        if city:
            loc["city"] = city
        r = requests.post("https://api.openai.com/v1/responses", timeout=TIMEOUT,
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": m, "input": q, "tools": [{"type": "web_search", "user_location": loc}]})
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


def ask_perplexity(q, city):
    key = env("PERPLEXITY_API_KEY")
    r = requests.post("https://api.perplexity.ai/v1/agent", timeout=TIMEOUT,
                      headers={"Authorization": f"Bearer {key}"},
                      json={"effort": os.environ.get("PERPLEXITY_EFFORT", "fast"), "input": q})
    r.raise_for_status()
    return responses_text(r.json())


def ask_gemini(q, city):
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


def ask_claude(q, city):
    key = env("ANTHROPIC_API_KEY")
    models = [m for m in [os.environ.get("ANTHROPIC_MODEL"), "claude-haiku-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"] if m]

    def call(m):
        tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3,
                "user_location": {"type": "approximate", "country": "GB", **({"city": city} if city else {})}}
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


def ask_google_aio(q, city):
    login, pw = env("DATAFORSEO_LOGIN"), env("DATAFORSEO_PASSWORD")
    r = requests.post("https://api.dataforseo.com/v3/serp/google/organic/live/advanced", timeout=TIMEOUT,
                      auth=(login, pw),
                      json=[{"keyword": q, "location_code": 2826, "language_code": "en", "device": "desktop",
                             "load_async_ai_overview": True}])
    r.raise_for_status()
    j = r.json()
    items = (((j.get("tasks") or [{}])[0].get("result") or [{}])[0].get("items")) or []
    aio = [i for i in items if i.get("type") == "ai_overview"]
    if not aio:
        return "", []
    parts, cites = [], []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("markdown", "text") and isinstance(v, str):
                    parts.append(v)
                elif k == "url" and isinstance(v, str):
                    cites.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(aio[0].get("markdown") and {"markdown": aio[0]["markdown"], "references": aio[0].get("references")} or aio[0])
    return "\n".join(dict.fromkeys(p.strip() for p in parts if p.strip())), cites


ASKERS = {"ChatGPT": ask_chatgpt, "Perplexity": ask_perplexity, "Gemini": ask_gemini,
          "Google AI Overviews": ask_google_aio, "Claude": ask_claude}


# ---------------------------------------------------------------- the "brain" (judging and writing)
def llm_json(instructions, content, schema_hint):
    """Ask OpenAI for a JSON answer. Used for planning, judging and writing."""
    if MOCK:
        return mock_llm(instructions, content)
    key = env("OPENAI_API_KEY")
    models = [m for m in [os.environ.get("JUDGE_MODEL"), "gpt-5.5-mini", "gpt-5.1-mini", "gpt-5-mini", "gpt-5.5"] if m]

    def call(m):
        r = requests.post("https://api.openai.com/v1/responses", timeout=TIMEOUT,
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": m, "instructions": instructions + "\nReply with JSON only, matching: " + schema_hint,
                                "input": content, "text": {"format": {"type": "json_object"}}})
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
    hint = '{"business_name":str,"name_variants":[str],"city":str,"trade_plural":str,"one_line_summary":str,"questions":[str x4]}'
    instr = ("You prepare an AI visibility check for a UK business. From the website text, identify the business name "
             "(and short variants people might use), the town or city it serves (empty if unclear), and what it does in plain words "
             "(trade_plural, e.g. 'accountants', 'wedding photographers'). Write exactly 4 questions a potential customer would "
             "type into an AI assistant when looking for this kind of business. Do NOT include the business name in these 4. "
             "Include the town/city when known. Keep each under 20 words, natural British English. "
             "If the customer supplied example searches, base 1-2 questions on them.")
    content = json.dumps({"website": audit.get("final_url", req["website"]), "title": audit.get("title"),
                          "meta_description": audit.get("meta_description"), "h1": audit.get("h1"),
                          "customer_example_searches": req.get("searches", ""), "website_text": site_text[:9000]})
    p = llm_json(instr, content, hint)
    qs = [q.strip() for q in p.get("questions", []) if q.strip()][:4]
    if len(qs) < 4 or not p.get("business_name"):
        raise RuntimeError("Could not work out the business or questions from the website")
    city = p.get("city", "").strip()
    qs.append(f"What do you know about {p['business_name']}{' in ' + city if city else ''}?")
    p["questions"] = qs
    return p


def ask_all(questions, city):
    jobs = {}
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for qi, q in enumerate(questions):
            for p in PLATFORMS:
                fn = mock_answer if MOCK else ASKERS[p]
                jobs[ex.submit(safe_ask, fn, p, q, city)] = (qi, p)
        out = {}
        for f in cf.as_completed(jobs):
            out[jobs[f]] = f.result()
    return out


def safe_ask(fn, platform, q, city):
    for attempt in range(2):
        try:
            if fn is mock_answer:
                return {"ok": True, **dict(zip(("text", "cites"), fn(platform, q, city)))}
            t, c = fn(q, city)
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

    facts = {"business": p["business_name"], "city": p.get("city"), "trade": p.get("trade_plural"),
             "overall_score": total_score, "recommendation_score": rec_score,
             "platforms_checked": plats, "platforms_mentioning_in_customer_questions": plats_found,
             "customer_question_answers_including_business": f"{rec_mentions} of {nrec * len(plats)}",
             "top_choice_count": sum(1 for r in results[:nrec] for v in r if v == 2),
             "about_business_answers": {pl: results[-1][j] for j, pl in enumerate(plats)},
             "answer_notes": {f"Q{qi + 1} {pl}": (verdicts.get((qi, pl)) or {}).get("note") for qi in range(len(questions)) for pl in plats},
             "competitors": competitors, "website_check": {k: v for k, v in audit.items() if k not in ("url",)}}
    hint = ('{"summary":[str,str,str],"opportunities":[{"title":str,"found":str,"action":str,"why":str,"effort":"Low"|"Medium"|"High"} x3]}')
    instr = ("You write a free AI visibility report for a UK small business owner, in calm, plain British English. No jargon, "
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
        "location": p.get("city") or "the UK", "date": dt.date.today().strftime("%-d %B %Y"),
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
    log("business:", p["business_name"], "|", p.get("city"), "| questions:", p["questions"])
    answers = ask_all(p["questions"], p.get("city", ""))
    log("answers collected:", sum(a["ok"] for a in answers.values()), "of", len(answers))
    verdicts = judge(p, p["questions"], answers, audit)
    data = build_data(req, p, audit, answers, verdicts)
    safe = re.sub(r"[^A-Za-z0-9]+", "-", data["business"]).strip("-")[:40] or "report"
    out = HERE / "out" / f"717Labs-AI-visibility-report-{safe}.pdf"
    out.parent.mkdir(exist_ok=True)
    (out.with_suffix(".json")).write_text(json.dumps({"request": {k: v for k, v in req.items() if k != "email"}, "data": data,
                                                      "answers": {f"{k[0]}|{k[1]}": v for k, v in answers.items()}}, indent=2))
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
def mock_answer(platform, q, city):
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
        return {"business_name": "Harbour Lane Dental", "name_variants": ["Harbour Lane"], "city": "Bristol",
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
