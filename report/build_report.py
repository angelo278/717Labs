"""Build a 717Labs AI visibility report PDF from a JSON file.
Usage: python3 build_report.py client.json [out.pdf]
"""
import json, sys, base64, os, html
from pathlib import Path

HERE = Path(__file__).parent
NAVY, BLUE = '#0F1B2D', '#2F6FEB'

def fonts():
    out = ''
    for fam, pk, ws in [('Plus Jakarta Sans', 'plus-jakarta-sans', [400, 500, 600, 700]), ('Inter', 'inter', [400, 500, 600])]:
        for w in ws:
            f = HERE / 'fonts' / f'{pk}-latin-{w}-normal.woff2'
            out += f"@font-face{{font-family:'{fam}';font-weight:{w};src:url(data:font/woff2;base64,{base64.b64encode(f.read_bytes()).decode()})}}"
    return out

e = html.escape
LOGO = f'<svg width="30" height="30" viewBox="0 0 36 36"><rect x="2" y="16" width="8" height="18" rx="2" fill="{NAVY}"/><rect x="14" y="4" width="8" height="30" rx="2" fill="{BLUE}"/><rect x="26" y="16" width="8" height="18" rx="2" fill="{NAVY}"/></svg>'

def band(score):
    if score < 40: return ('Rarely mentioned by AI', 'low')
    if score < 70: return ('Sometimes mentioned by AI', 'mid')
    return ('Regularly recommended by AI', 'high')

def dial(score, size):
    fill = 75 * score / 100
    ticks = ''.join(f'<line x1="100" y1="3" x2="100" y2="8" transform="rotate({90 + i * 27} 100 100)"/>' for i in range(11))
    return f'''<div class="dial" style="width:{size}px;height:{size}px">
<svg viewBox="0 0 200 200"><defs><linearGradient id="g{size}" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#7FA8F5"/><stop offset="1" stop-color="{BLUE}"/></linearGradient></defs>
<g transform="rotate(135 100 100)"><circle cx="100" cy="100" r="80" pathLength="100" class="trk"/>
<circle cx="100" cy="100" r="80" pathLength="100" class="bar" stroke="url(#g{size})" stroke-dasharray="{fill:.2f} 100"/>
<g class="ticks">{ticks}</g></g></svg>
<div class="num" style="font-size:{size * .27:.0f}px">{score}<small style="font-size:{size * .075:.0f}px">/100</small></div></div>'''

SYM = {0: ('', 'Not mentioned', 's0'), 1: ('', 'Mentioned', 's1'), 2: ('', 'Recommended', 's2')}

def build(d):
    P, Q, R = d['platforms'], d['questions'], d['results']
    nrec = len(Q) - 1                      # last question is the "about your business" question
    total = sum(map(sum, R)); maxp = 2 * len(P) * len(Q)
    score = round(total / maxp * 100)
    label, cls = band(score)
    rec_mentions = sum(1 for q in R[:nrec] for v in q if v > 0)
    plats_found = sum(1 for j in range(len(P)) if any(R[i][j] > 0 for i in range(nrec)))
    biz = e(d['business'])
    sample = d.get('sample')

    def foot(n):
        s = ' · Sample report for a fictional business' if sample else ''
        return f'<div class="foot"><span>{LOGO}<b>717Labs</b></span><span>AI visibility report · {biz}{s}</span><span>{n}</span></div>'

    pages = []
    # 1 cover
    pages.append(f'''<section class="page cover">
<div class="brand">{LOGO}<b>717Labs</b></div>
{'<div class="samplebar">Sample report · fictional business, for illustration only</div>' if sample else ''}
<div class="cover-main"><div class="eyebrow">Free AI visibility report</div>
<h1>How AI sees<br>{biz}</h1>
<p class="cover-sub">Whether {e(', '.join(P[:-1]) + ' and ' + P[-1])} mention or recommend your business, who appears instead, and what to do about it.</p></div>
<div class="cover-card">{dial(score, 230)}<div class="cover-meta"><div class="pill {cls}"><i></i>{label}</div>
<dl><dt>Business</dt><dd>{biz}</dd><dt>Website</dt><dd>{e(d['website'])}</dd><dt>Area</dt><dd>{e(d['location'])}</dd><dt>Assessment date</dt><dd>{e(d['date'])}</dd></dl></div></div>
<div class="cover-foot">Prepared by 717Labs · info@717labs.co.uk · 717labs.co.uk</div></section>''')

    # 2 summary
    comps = d['competitors']
    rows = [(c['name'], c['score'], False) for c in comps] + [(d['business'], d.get('rec_score', score), True)]
    rows.sort(key=lambda r: -r[1])
    bars = ''.join(f'<div class="cbar{" you" if y else ""}"><div class="cbar-top"><span>{e(n)}{" (you)" if y else ""}</span><b>{s}/100</b></div><div class="trackl"><div style="width:{s}%"></div></div></div>' for n, s, y in rows)
    opps = ''.join(f'<li><span class="n">{i + 1}</span>{e(o["title"])}</li>' for i, o in enumerate(d['opportunities']))
    pages.append(f'''<section class="page">
<div class="kicker">Summary</div><h2>Your AI visibility at a glance</h2>
<div class="sum-top"><div class="sum-dial">{dial(score, 190)}<div class="pill {cls}"><i></i>{label}</div></div>
<div class="stats"><div class="stat"><b>{score}<small>/100</small></b><span>AI visibility score</span></div>
<div class="stat"><b>{plats_found}<small> of {len(P)}</small></b><span>AI platforms mentioned you when asked for a recommendation</span></div>
<div class="stat"><b>{rec_mentions}<small> of {nrec * len(P)}</small></b><span>recommendation answers that included you</span></div></div></div>
<h3>What we found</h3><ul class="finds">{''.join(f'<li>{e(s)}</li>' for s in d['summary'])}</ul>
<div class="two"><div><h3>You and your competitors</h3>{bars}{'<p class="small">Based on the customer questions only, so every business is measured the same way.</p>' if d.get('rec_score') is not None else ''}</div>
<div><h3>Your three biggest opportunities</h3><ol class="opps">{opps}</ol><p class="small">Details on page 5.</p></div></div>
{foot(2)}</section>''')

    # 3 visibility grid
    head = ''.join(f'<th>{e(p)}</th>' for p in P)
    body = ''
    for i, q in enumerate(Q):
        cells = ''.join(f'<td><span class="sym {SYM[v][2]}">{SYM[v][0]}</span></td>' for v in R[i])
        tag = '<em>About your business</em>' if i == nrec else f'<em>Recommendation {i + 1}</em>'
        body += f'<tr><td class="q">{tag}{e(q)}</td>{cells}</tr>'
    per = []
    for j, p in enumerate(P):
        pts = sum(R[i][j] for i in range(len(Q))); ps = round(pts / (2 * len(Q)) * 100)
        per.append(f'<div class="pcard"><span>{e(p)}</span><b>{ps}</b><div class="trackl"><div style="width:{max(ps, 2)}%"></div></div></div>')
    pages.append(f'''<section class="page">
<div class="kicker">Section 1</div><h2>Do AI platforms mention you?</h2>
<p class="lead">We asked each platform the questions a potential customer in {e(d['location'])} might ask, and recorded whether your business appeared in the answer.</p>
<table class="grid"><thead><tr><th class="q">Question asked</th>{head}</tr></thead><tbody>{body}</tbody></table>
<div class="legend"><span><span class="sym s2"></span> Recommended: named as a top choice, or described accurately</span><span><span class="sym s1"></span> Mentioned: included, but not as a top choice, or described with errors</span><span><span class="sym s0"></span> Not mentioned</span></div>
<h3>Score by platform</h3><div class="pcards">{''.join(per)}</div>
{foot(3)}</section>''')

    # 4 competitors
    crow = ''.join(f'<tr><td><b>{e(c["name"])}</b></td><td>{c["appearances"]} of {nrec * len(P)}</td><td>{c["score"]}/100</td></tr>' for c in comps)
    crow += f'<tr class="you"><td><b>{biz} (you)</b></td><td>{rec_mentions} of {nrec * len(P)}</td><td>{d.get("rec_score", score)}/100</td></tr>'
    shots = ''
    for s in d['screenshots']:
        if s.get('image'):
            img = base64.b64encode(Path(s['image']).read_bytes()).decode()
            ext = Path(s['image']).suffix.lstrip('.').replace('jpg', 'jpeg')
            inner = f'<img src="data:image/{ext};base64,{img}">'
        else:
            inner = '<div class="chat"><div class="qbub">' + e(s['question']) + '</div><div class="abub">' + ''.join(f'<p>{e(l)}</p>' for l in s['answer']) + '</div></div>'
        shots += f'<figure class="shot"><div class="shot-top"><b>{e(s["platform"])}</b><span>{e(s["question"])}</span></div>{inner}<figcaption class="{'pos' if s.get('status', 0) > 0 else ''}">{e(s.get("note", ""))}</figcaption></figure>'
    pages.append(f'''<section class="page">
<div class="kicker">Section 2</div><h2>Who appears instead of you</h2>
<p class="lead">These are the businesses AI platforms named most often across the {nrec * len(P)} recommendation answers we checked.</p>
<table class="ctable"><thead><tr><th>Business</th><th>Times recommended or mentioned</th><th>{'Recommendation score' if d.get('rec_score') is not None else 'AI visibility score'}</th></tr></thead><tbody>{crow}</tbody></table>
<h3>Examples from the answers</h3><div class="shots">{shots}</div>
{'<p class="small">In this sample the answers are recreated for illustration. Real reports show the actual answers.</p>' if sample else f'<p class="small">Answers captured from each platform on {e(d["date"])} and shortened to fit. AI answers can vary from one search to the next.</p>'}
{foot(4)}</section>''')

    # 5 opportunities
    oc = ''.join(f'''<div class="opp"><div class="opp-h"><span class="n">{i + 1}</span><h4>{e(o["title"])}</h4><span class="effort">Effort: {e(o["effort"])}</span></div>
<div class="opp-b"><div><em>What we found</em><p>{e(o["found"])}</p></div><div><em>What to do</em><p>{e(o["action"])}</p></div><div><em>Why it matters</em><p>{e(o["why"])}</p></div></div></div>''' for i, o in enumerate(d['opportunities']))
    pages.append(f'''<section class="page">
<div class="kicker">Section 3</div><h2>Your three biggest opportunities</h2>
<p class="lead">The three changes we believe could make the greatest difference to how often AI recommends {biz}.</p>
{oc}{foot(5)}</section>''')

    # 6 method + next steps
    pages.append(f'''<section class="page">
<div class="kicker">About this report</div><h2>How we measured, and what happens next</h2>
<div class="two"><div><h3>How we measured</h3>
<p>We asked {len(P)} AI platforms the same {len(Q)} questions: {nrec} that a potential customer might ask, and one asking about your business by name. That gives {len(P) * len(Q)} answers.{(' Questions were asked through each platform&rsquo;s official connection with web search switched on, set to ' + e(d.get('country') or 'your country') + '.') if d.get('captured') else ''}</p>
<p>Each answer scores 2 points if you are recommended or described accurately, 1 point if you are mentioned or described with errors, and 0 if you do not appear. Your score is your share of the {maxp} available points, out of 100. Competitors are scored the same way.</p>
<h3>Score bands</h3><div class="bands"><div><b>0–39</b>Rarely mentioned by AI</div><div><b>40–69</b>Sometimes mentioned by AI</div><div><b>70–100</b>Regularly recommended by AI</div></div>
<p class="small">AI answers change over time and can vary between users and locations. This report is a snapshot taken on {e(d['date'])} and is a guide, not a guarantee of future results.</p></div>
<div class="next"><h3>Next steps</h3>
<p>This report is yours to keep, and there is no obligation to become a client.</p>
<p>If you would like help putting these changes in place, we offer a monthly service shaped around your business. We handle the research, the improvements and the reporting, and you approve every change.</p>
<div class="cta"><b>Book a short call</b><span>Reply to this email or write to info@717labs.co.uk</span></div>
<p class="tag">Be seen where it matters most.</p></div></div>
{foot(6)}</section>''')

    zooms = d.get('zoom', [1.12, 1.32, 1.32, 1.3, 1.2, 1.36])
    out = []
    for k, pgh in enumerate(pages):
        a = pgh.index('>') + 1
        fi = pgh.rfind('<div class="foot">')
        if fi < 0: fi = pgh.rfind('</section>')
        out.append(pgh[:a] + f'<div class="inner" style="zoom:{zooms[k]}">' + pgh[a:fi] + '</div>' + pgh[fi:])
    pages = out
    css = f'''{fonts()}
@page{{size:A4;margin:0}}*{{box-sizing:border-box}}
body{{margin:0;font-family:'Plus Jakarta Sans',Inter,sans-serif;color:{NAVY};-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.page{{width:210mm;height:297mm;padding:18mm 17mm 22mm;position:relative;page-break-after:always;overflow:hidden;background:#fff}}
.page:last-child{{page-break-after:auto}}
h1,h2,h3,h4{{margin:0;letter-spacing:-.02em}}
p{{margin:0 0 9px;font-size:11.5px;line-height:1.6;color:#3B4A60}}
.kicker{{font-size:10.5px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:{BLUE};margin-bottom:6px}}
h2{{font-size:26px;font-weight:700;margin-bottom:10px}}
h3{{font-size:14px;font-weight:700;margin:20px 0 10px}}
.lead{{font-size:12.5px;max-width:150mm;margin-bottom:14px}}
.small{{font-size:9.5px;color:#6B7A90}}
.foot{{position:absolute;left:17mm;right:17mm;bottom:9mm;display:flex;justify-content:space-between;align-items:center;font-size:9px;color:#6B7A90;border-top:1px solid #E3E8F0;padding-top:8px}}
.foot span:first-child{{display:flex;align-items:center;gap:6px;color:{NAVY};font-family:Inter}}.foot svg{{width:14px;height:14px}}
.brand{{display:flex;align-items:center;gap:10px;font-family:Inter;font-size:20px;font-weight:600}}
.cover .inner{{display:flex;flex-direction:column;height:calc((297mm - 40mm)/1.12)}}.cover{{background:linear-gradient(180deg,#fff 0,#fff 55%,#F2F6FD 100%);display:flex;flex-direction:column}}
.samplebar{{margin-top:14px;align-self:flex-start;font-size:10px;font-weight:600;color:#B45309;background:#FEF3E2;border-radius:999px;padding:5px 12px}}
.cover-main{{margin-top:30mm}}.eyebrow{{font-size:12px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:{BLUE};margin-bottom:12px}}
.cover h1{{font-size:44px;line-height:1.08;font-weight:700}}
.cover-sub{{font-size:13.5px;max-width:140mm;margin-top:14px}}
.cover-card{{margin-top:16mm;display:flex;gap:26px;align-items:center;background:#fff;border:1px solid #E3E8F0;border-radius:22px;padding:22px 26px;box-shadow:0 20px 40px -24px rgba(15,27,45,.25)}}
.cover-meta{{flex:1}}dl{{display:grid;grid-template-columns:auto 1fr;gap:7px 16px;margin:14px 0 0;font-size:12px}}dt{{color:#6B7A90}}dd{{margin:0;font-weight:600}}
.cover-foot{{margin-top:auto;font-size:10.5px;color:#6B7A90}}
.dial{{position:relative;flex:none}}.dial svg{{width:100%;height:100%}}
.trk,.bar{{fill:none;stroke-width:14;stroke-linecap:round}}.trk{{stroke:#EDF1F7;stroke-dasharray:75 100}}
.ticks line{{stroke:#D5DEEA;stroke-width:1.5;stroke-linecap:round}}
.num{{position:absolute;inset:0;display:flex;align-items:baseline;justify-content:center;padding-top:35%;font-weight:700;letter-spacing:-.04em;line-height:1}}.num small{{font-weight:600;color:#6B7A90;margin-left:2px;letter-spacing:0}}
.pill{{display:inline-flex;align-items:center;gap:7px;font-size:11px;font-weight:600;border-radius:999px;padding:5px 11px}}.pill i{{width:7px;height:7px;border-radius:50%}}
.pill.low{{color:#B45309;background:#FEF3E2}}.pill.low i{{background:#F59E0B}}
.pill.mid{{color:#1D4ED8;background:#EAF1FE}}.pill.mid i{{background:{BLUE}}}
.pill.high{{color:#047857;background:#E7F7EF}}.pill.high i{{background:#10B981}}
.sum-top{{display:flex;gap:26px;align-items:center;background:#F5F7FB;border:1px solid #E3E8F0;border-radius:20px;padding:18px 22px;margin-top:8px}}
.sum-dial{{display:flex;flex-direction:column;align-items:center;gap:6px}}
.stats{{flex:1;display:grid;gap:10px}}.stat{{background:#fff;border:1px solid #E3E8F0;border-radius:14px;padding:11px 14px;display:flex;align-items:center;gap:14px}}
.stat b{{font-size:24px;font-weight:700;min-width:82px;letter-spacing:-.02em}}.stat b small{{font-size:12px;color:#6B7A90;font-weight:600}}.stat span{{font-size:11.5px;color:#3B4A60;line-height:1.4}}
.finds{{margin:0;padding:0;list-style:none;display:grid;gap:8px}}.finds li{{font-size:11.5px;line-height:1.55;color:#3B4A60;padding-left:16px;position:relative}}.finds li:before{{content:"";position:absolute;left:0;top:7px;width:6px;height:6px;border-radius:50%;background:{BLUE}}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}
.cbar{{margin-bottom:10px}}.cbar-top{{display:flex;justify-content:space-between;font-size:11px;margin-bottom:5px;color:#3B4A60}}.cbar.you .cbar-top{{color:{NAVY};font-weight:600}}
.trackl{{height:8px;border-radius:99px;background:#EDF1F7;overflow:hidden}}.trackl div{{height:100%;border-radius:99px;background:#C3D2E6}}.cbar.you .trackl div,.pcard .trackl div{{background:{BLUE}}}
.opps{{margin:0;padding:0;list-style:none;display:grid;gap:8px}}.opps li{{display:flex;gap:10px;align-items:center;font-size:11.5px;font-weight:600;background:#F5F7FB;border:1px solid #E3E8F0;border-radius:12px;padding:9px 11px}}
.n{{flex:none;width:22px;height:22px;border-radius:50%;background:{BLUE};color:#fff;display:grid;place-items:center;font-size:11px;font-weight:700}}
.grid{{width:100%;border-collapse:separate;border-spacing:0;font-size:10.5px;border:1px solid #E3E8F0;border-radius:14px;overflow:hidden}}
.grid th{{background:#F5F7FB;font-weight:600;padding:9px 4px;font-size:9.5px;color:#3B4A60;text-align:center}}.grid th.q{{text-align:left;padding-left:12px}}
.grid td{{border-top:1px solid #E3E8F0;padding:9px 4px;text-align:center}}.grid td.q{{text-align:left;padding:9px 12px;line-height:1.45;width:46%}}.grid td.q em{{display:block;font-style:normal;font-size:8.5px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:{BLUE};margin-bottom:2px}}
.grid tr:last-child td{{background:#FAFBFD}}
.sym{{display:inline-block;width:14px;height:14px;border-radius:50%;vertical-align:middle}}.s2{{background:{BLUE}}}.s1{{background:linear-gradient(90deg,{BLUE} 50%,#fff 50%);border:2px solid {BLUE}}}.s0{{border:2px solid #C9D3E1}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;font-size:9.5px;color:#6B7A90;margin-top:9px}}.legend .sym{{width:11px;height:11px;margin-right:3px}}.legend .s1{{border-width:1.5px}}
.pcards{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.pcard{{border:1px solid #E3E8F0;border-radius:14px;padding:12px 14px;display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center}}
.pcard span{{font-size:11px;font-weight:600}}.pcard b{{font-size:18px}}.pcard .trackl{{grid-column:1/-1}}
.ctable{{width:100%;border-collapse:collapse;font-size:11px}}.ctable th{{text-align:left;font-size:9.5px;font-weight:600;color:#6B7A90;padding:7px 10px;border-bottom:1px solid #E3E8F0}}
.ctable td{{padding:10px;border-bottom:1px solid #E3E8F0}}.ctable tr.you td{{background:#EAF1FE;color:{NAVY}}}
.shots{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.shot{{margin:0;border:1px solid #E3E8F0;border-radius:16px;overflow:hidden;background:#fff}}
.shot-top{{display:flex;flex-direction:column;gap:2px;padding:10px 12px;background:#F5F7FB;border-bottom:1px solid #E3E8F0;font-size:10px}}.shot-top span{{color:#6B7A90}}
.shot img{{display:block;width:100%}}.chat{{padding:12px;display:grid;gap:8px}}
.qbub{{justify-self:end;max-width:85%;background:{NAVY};color:#fff;font-size:10px;border-radius:12px 12px 2px 12px;padding:7px 10px}}
.abub{{background:#F5F7FB;border-radius:12px 12px 12px 2px;padding:9px 11px}}.abub p{{font-size:9.8px;margin:0 0 4px;line-height:1.5}}
.shot figcaption{{font-size:10px;font-weight:600;color:#B45309;padding:8px 12px 11px}}.shot figcaption.pos{{color:{BLUE}}}
.opp{{border:1px solid #E3E8F0;border-radius:18px;margin-bottom:12px;overflow:hidden}}
.opp-h{{display:flex;align-items:center;gap:11px;padding:12px 16px;background:#F5F7FB;border-bottom:1px solid #E3E8F0}}.opp-h h4{{flex:1;font-size:14px;font-weight:700}}
.effort{{font-size:9.5px;font-weight:600;color:#1D4ED8;background:#EAF1FE;border-radius:999px;padding:4px 10px}}
.opp-b{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;padding:12px 16px 6px}}.opp-b em{{display:block;font-style:normal;font-size:9px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:{BLUE};margin-bottom:4px}}.opp-b p{{font-size:10.8px}}
.bands{{display:grid;gap:7px}}.bands div{{display:flex;gap:12px;font-size:11px;align-items:center;border:1px solid #E3E8F0;border-radius:10px;padding:8px 11px;color:#3B4A60}}.bands b{{color:{NAVY};min-width:46px}}
.next{{background:#F5F7FB;border:1px solid #E3E8F0;border-radius:20px;padding:4px 20px 20px;align-self:start}}
.cta{{background:{BLUE};color:#fff;border-radius:14px;padding:14px 16px;display:grid;gap:3px;margin:14px 0}}.cta b{{font-size:15px}}.cta span{{font-size:11px;opacity:.9}}
.tag{{font-weight:700;color:{NAVY};font-size:13px;margin:0}}
'''
    return f'<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><title>AI visibility report: {biz}</title><style>{css}</style></head><body>{"".join(pages)}</body></html>', score

def render_pdf(d, out):
    doc, score = build(d)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page()
        pg.set_content(doc); pg.wait_for_timeout(400)
        pg.pdf(path=out, format='A4', print_background=True, margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
        b.close()
    return score


if __name__ == '__main__':
    src = sys.argv[1]; d = json.load(open(src))
    out = sys.argv[2] if len(sys.argv) > 2 else str(Path(src).with_suffix('.pdf'))
    print(out, 'score', render_pdf(d, out))
