#!/usr/bin/env python3
"""Render history.json -> index.html : a two-direction weekend flight board.
Departure-board aesthetic. Server-rendered, self-contained, safe to host."""

import os
import json
import html
from datetime import datetime, date

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "index.html")
LABELS = {"forward": "SF → New York", "reverse": "New York → SF"}


def load():
    p = os.path.join(HERE, "history.json")
    if not os.path.exists(p):
        return {"forward": {}, "reverse": {}}
    with open(p) as f:
        h = json.load(f)
    return {"forward": h.get("forward", {}), "reverse": h.get("reverse", {})}


def tlabel(iso):
    d = datetime.fromisoformat(iso if len(iso) > 10 else iso + "T00:00")
    h = d.hour % 12 or 12
    ap = "a" if d.hour < 12 else "p"
    return f"{d:%a} {h}:{d.minute:02d}{ap}"


def dur(mins):
    if not mins:
        return ""
    h, m = divmod(int(mins), 60)
    return (f"{h}h {m}m" if h and m else f"{h}h" if h else f"{m}m")


def leg_html(leg, arrow):
    segs = leg["segs"]
    a, b = segs[0], segs[-1]
    if leg["stops"] == 0:
        node = '<span class="line"><span class="ns">nonstop</span></span>'
    else:
        chips = []
        for lay in leg["layovers"]:
            long = (lay.get("dur") or 0) > 180
            chips.append(f'<span class="stopnode{" long" if long else ""}">'
                         f'{html.escape(lay["id"] or "?")} · {dur(lay.get("dur"))}</span>')
        node = '<span class="line">' + "".join(chips) + '</span>'
    return f"""<div class="leg">
      <span class="dir">{arrow}</span>
      <span class="end"><b>{html.escape(a['f'])}</b><i>{tlabel(a['ft'])}</i></span>
      {node}
      <span class="end"><b>{html.escape(b['t'])}</b><i>{tlabel(b['tt'])}</i></span>
    </div>"""


def airlines_of(c):
    al = []
    for leg in (c["out"], c["ret"]):
        for s in leg["segs"]:
            if s.get("al") and s["al"] not in al:
                al.append(s["al"])
    return ", ".join(al)


def card_html(rec, rank, best):
    c = rec["confirmed"]
    fri = datetime.fromisoformat(rec["friday"])
    sun = datetime.fromisoformat(rec["sunday"])
    dout = (fri.date() - date.today()).days
    stops = c["out"]["stops"] + c["ret"]["stops"]
    sbadge = ('<span class="badge ns">nonstop both ways</span>' if stops == 0
              else f'<span class="badge con">{stops} connection{"s" if stops > 1 else ""}</span>')
    checked = datetime.fromisoformat(c["checked"]).strftime("%b %-d, %-I:%M%p").lower()
    return f"""<article class="card{' best' if best else ''}">
    <div class="meta">
      <span class="rank">{rank:02d}</span>
      <div class="when">
        <b>{fri:%b %-d} &ndash; {sun:%b %-d}</b>
        <span>{dout} days out{' · best price' if best else ''}</span>
      </div>
    </div>
    <div class="itin">
      {leg_html(c['out'], 'OUT')}
      {leg_html(c['ret'], 'BACK')}
      <div class="sub">{sbadge}<span class="al">{html.escape(airlines_of(c))}</span></div>
    </div>
    <div class="buy">
      <div class="price">${c['price']:.0f}</div>
      <a class="cta" href="{html.escape(c['link'])}" target="_blank" rel="noopener">Google Flights &#8599;</a>
      <span class="checked">checked {checked}</span>
    </div>
  </article>"""


def tab_html(book):
    confirmed = [r for r in book.values() if r.get("confirmed")]
    confirmed.sort(key=lambda r: r["confirmed"]["price"])
    watching = [r for r in book.values()
                if not r.get("confirmed") and r.get("radar_low")]
    if not confirmed and not watching:
        return ('<p class="empty">No priced weekends yet. The radar sweeps every '
                '3 hours &mdash; real itineraries appear here as fares come in.</p>')
    best_price = confirmed[0]["confirmed"]["price"] if confirmed else None
    cards = "".join(card_html(r, i, r["confirmed"]["price"] == best_price)
                    for i, r in enumerate(confirmed, 1))
    watch = ""
    if watching:
        watching.sort(key=lambda r: r["radar_low"])
        items = "".join(
            f'<li><b>{datetime.fromisoformat(r["friday"]):%b %-d}</b> '
            f'&mdash; radar ~${r["radar_low"]:.0f}, no schedule match yet</li>'
            for r in watching)
        watch = (f'<details class="watch"><summary>{len(watching)} more weekend'
                 f'{"s" if len(watching) != 1 else ""} watching (radar only)</summary>'
                 f'<ul>{items}</ul></details>')
    return cards + watch


def build():
    hist = load()
    allc = [r["confirmed"]["price"] for b in hist.values()
            for r in b.values() if r.get("confirmed")]
    cheapest = f"${min(allc):.0f}" if allc else "—"
    n_priced = sum(1 for b in hist.values() for r in b.values() if r.get("confirmed"))
    updated = datetime.now().strftime("%b %-d, %Y · %-I:%M %p")

    tabs_nav = "".join(
        f'<button class="tab{" on" if i == 0 else ""}" data-tab="{k}">{LABELS[k]}</button>'
        for i, k in enumerate(("forward", "reverse")))
    panels = "".join(
        f'<section class="panel{" on" if i == 0 else ""}" id="p-{k}">{tab_html(hist.get(k, {}))}</section>'
        for i, k in enumerate(("forward", "reverse")))

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weekend Bridge — SF ⇄ NYC</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=Space+Mono:wght@400;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{{
    --void:#0a0e1c; --panel:#121a30; --panel2:#0f1628; --line:#243052;
    --ink:#eef2ff; --muted:#8a97be; --amber:#ffb457; --sky:#5ec8ec;
    --ns:#5fd7a6; --warn:#ff9d5c;
    --mono:'Space Mono',ui-monospace,monospace;
    --disp:'Space Grotesk',system-ui,sans-serif;
    --body:'Inter',system-ui,sans-serif;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:
      radial-gradient(1200px 500px at 80% -10%, #16234a 0%, transparent 60%),
      var(--void);
    color:var(--ink);font-family:var(--body);-webkit-font-smoothing:antialiased;line-height:1.5}}
  .wrap{{max-width:920px;margin:0 auto;padding:40px 20px 72px}}
  /* header */
  .eyebrow{{font-family:var(--mono);font-size:.72rem;letter-spacing:.32em;color:var(--amber);
    text-transform:uppercase;margin:0 0 10px}}
  h1{{font-family:var(--disp);font-weight:700;font-size:clamp(2.1rem,6vw,3.2rem);
    letter-spacing:-.02em;margin:0;line-height:1}}
  h1 .swap{{color:var(--sky)}}
  .rules{{color:var(--muted);font-size:.9rem;margin:14px 0 0;max-width:52ch}}
  .topline{{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;
    flex-wrap:wrap;margin-top:26px;padding-bottom:22px;border-bottom:1px solid var(--line)}}
  .cheap{{font-family:var(--mono)}}
  .cheap b{{display:block;font-size:2rem;color:var(--amber);font-weight:700;line-height:1}}
  .cheap span,.upd{{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.12em}}
  .upd{{text-align:right}}
  /* tabs */
  .tabs{{display:flex;gap:6px;margin:26px 0 22px;background:var(--panel2);
    border:1px solid var(--line);border-radius:999px;padding:5px;width:fit-content}}
  .tab{{font-family:var(--disp);font-weight:500;font-size:.92rem;color:var(--muted);
    background:none;border:0;padding:9px 18px;border-radius:999px;cursor:pointer;
    transition:.18s color,.18s background}}
  .tab:hover{{color:var(--ink)}}
  .tab.on{{color:var(--void);background:var(--amber);font-weight:700}}
  .tab:focus-visible{{outline:2px solid var(--sky);outline-offset:2px}}
  .panel{{display:none}} .panel.on{{display:block}}
  /* card */
  .card{{display:grid;grid-template-columns:150px 1fr 150px;gap:18px;align-items:center;
    background:linear-gradient(180deg,var(--panel),var(--panel2));
    border:1px solid var(--line);border-radius:16px;padding:18px 20px;margin-bottom:12px;
    animation:rise .5s both}}
  .card.best{{border-color:var(--amber);box-shadow:0 0 0 1px var(--amber),0 12px 40px -18px var(--amber)}}
  @keyframes rise{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:none}}}}
  @media (prefers-reduced-motion:reduce){{.card{{animation:none}}}}
  .meta{{display:flex;gap:14px;align-items:center}}
  .rank{{font-family:var(--mono);font-size:1.05rem;color:var(--muted)}}
  .card.best .rank{{color:var(--amber)}}
  .when b{{font-family:var(--disp);font-weight:700;font-size:1.05rem;display:block}}
  .when span{{font-size:.74rem;color:var(--muted)}}
  /* itinerary ribbon */
  .itin{{min-width:0}}
  .leg{{display:flex;align-items:center;gap:12px;padding:5px 0}}
  .dir{{font-family:var(--mono);font-size:.62rem;color:var(--muted);width:34px;letter-spacing:.08em}}
  .end{{text-align:center;font-family:var(--mono);flex:0 0 auto}}
  .end b{{display:block;font-size:1.05rem;letter-spacing:.04em}}
  .end i{{font-style:normal;font-size:.72rem;color:var(--muted)}}
  .line{{flex:1;display:flex;align-items:center;justify-content:center;gap:6px;
    position:relative;min-width:60px;border-top:1px dashed var(--line);margin-top:-14px;height:1px}}
  .line .ns,.line .stopnode{{position:relative;top:-10px;font-family:var(--mono);font-size:.66rem;
    background:var(--panel);padding:2px 8px;border-radius:999px;border:1px solid var(--line)}}
  .line .ns{{color:var(--ns)}}
  .stopnode{{color:var(--sky)}} .stopnode.long{{color:var(--warn);border-color:var(--warn)}}
  .sub{{display:flex;gap:12px;align-items:center;margin-top:8px;flex-wrap:wrap}}
  .badge{{font-family:var(--mono);font-size:.66rem;padding:3px 9px;border-radius:999px;
    text-transform:uppercase;letter-spacing:.08em}}
  .badge.ns{{color:var(--ns);background:rgba(95,215,166,.12)}}
  .badge.con{{color:var(--warn);background:rgba(255,157,92,.12)}}
  .al{{font-size:.78rem;color:var(--muted)}}
  /* buy */
  .buy{{text-align:right}}
  .price{{font-family:var(--mono);font-weight:700;font-size:1.9rem;line-height:1}}
  .card.best .price{{color:var(--amber)}}
  .cta{{display:inline-block;margin-top:8px;font-family:var(--disp);font-weight:500;font-size:.82rem;
    color:var(--sky);text-decoration:none;border:1px solid var(--line);border-radius:8px;
    padding:6px 12px;transition:.18s}}
  .cta:hover{{border-color:var(--sky);background:rgba(94,200,236,.08)}}
  .checked{{display:block;margin-top:7px;font-size:.64rem;color:var(--muted)}}
  /* watch + footer */
  .watch{{margin-top:8px;border:1px solid var(--line);border-radius:12px;padding:4px 16px;background:var(--panel2)}}
  .watch summary{{cursor:pointer;font-size:.84rem;color:var(--muted);padding:10px 0}}
  .watch ul{{margin:0 0 12px;padding-left:18px;color:var(--muted);font-size:.84rem}}
  .empty{{color:var(--muted);text-align:center;padding:40px}}
  footer{{margin-top:30px;color:var(--muted);font-size:.76rem;line-height:1.7;
    border-top:1px solid var(--line);padding-top:18px}}
  footer b{{color:var(--ink)}}
  @media(max-width:640px){{
    .card{{grid-template-columns:1fr;gap:12px}}
    .buy{{text-align:left;display:flex;align-items:center;gap:14px}}
    .checked{{margin-top:0}}
    .line{{margin-top:0;border:0}} .line .ns,.line .stopnode{{top:0}}
  }}
</style>
</head>
<body>
  <div class="wrap">
    <p class="eyebrow">Weekend Bridge</p>
    <h1>SF <span class="swap">&#8644;</span> NYC</h1>
    <p class="rules">Real round trips for a weekend across the country. Out Friday 8 PM or later,
       back Sunday afternoon landing at night, at most one connection &mdash; ranked cheapest first.</p>

    <div class="topline">
      <div class="cheap"><span>Cheapest right now</span><b>{cheapest}</b></div>
      <div class="upd">{n_priced} live itineraries<br>updated {updated}</div>
    </div>

    <div class="tabs" role="tablist">{tabs_nav}</div>
    {panels}

    <footer>
      Every price here is a <b>real Google Flights round trip</b> pulled for these exact dates and rules &mdash;
      click <b>Google Flights</b> on any card to see it live and book. Connection chips show the layover
      airport and duration; <span style="color:var(--warn)">amber</span> means a long layover (3h+).
      Fares move constantly, so confirm on Google Flights before you buy. Refreshes every 3 hours.
    </footer>
  </div>
<script>
  document.querySelectorAll('.tab').forEach(function(t){{
    t.addEventListener('click',function(){{
      document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
      document.querySelectorAll('.panel').forEach(x=>x.classList.remove('on'));
      t.classList.add('on');
      document.getElementById('p-'+t.dataset.tab).classList.add('on');
    }});
  }});
</script>
</body>
</html>"""
    with open(OUT, "w") as f:
        f.write(doc)
    print(f"wrote {OUT} ({n_priced} live itineraries)")


if __name__ == "__main__":
    build()
