#!/usr/bin/env python3
"""Render history.json -> index.html : a ranked SFO<->NYC weekend price board.
Server-rendered (no external assets), theme-aware, safe to host on GitHub Pages."""

import os
import json
from datetime import datetime, date

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(HERE, "history.json")
OUT = os.path.join(HERE, "index.html")


def load():
    if not os.path.exists(HISTORY):
        return {}
    with open(HISTORY) as f:
        return json.load(f)


def current_price(rec):
    s = rec.get("samples") or []
    return s[-1]["radar"] if s else None


def sparkline(rec, w=90, h=24):
    vals = [x["radar"] for x in (rec.get("samples") or []) if x.get("radar")]
    if len(vals) < 2:
        return '<span class="spark-empty">—</span>'
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    step = w / (len(vals) - 1)
    pts = " ".join(f"{i*step:.1f},{h - (v-lo)/rng*(h-4) - 2:.1f}"
                   for i, v in enumerate(vals))
    down = vals[-1] <= vals[0]
    cls = "down" if down else "up"
    return (f'<svg class="spark {cls}" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'preserveAspectRatio="none"><polyline points="{pts}" '
            f'fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')


def fmt_weekend(rec):
    fri = datetime.fromisoformat(rec["friday"])
    sun = datetime.fromisoformat(rec["sunday"])
    return f"{fri:%a %b %-d} – {sun:%a %b %-d}"


def days_out(rec):
    return (datetime.fromisoformat(rec["friday"]).date() - date.today()).days


def build():
    hist = load()
    recs = list(hist.values())
    live = [r for r in recs if current_price(r) is not None]
    waiting = [r for r in recs if current_price(r) is None]
    live.sort(key=lambda r: current_price(r))

    rows = []
    for i, r in enumerate(live, 1):
        cur = current_price(r)
        low = r.get("radar_low") or cur
        conf = r.get("confirmed_low")
        delta = cur - low
        delta_html = (f'<span class="delta flat">at low</span>' if delta <= 0.5
                      else f'<span class="delta up">+${delta:.0f}</span>')
        conf_html = (f'${conf:.0f}' if conf else
                     '<span class="muted" title="set once a drop is confirmed on Google Flights">—</span>')
        medal = {1: "\U0001F947", 2: "\U0001F948", 3: "\U0001F949"}.get(i, f"{i}")
        rows.append(f"""
      <tr>
        <td class="rank">{medal}</td>
        <td class="wk">{fmt_weekend(r)}<span class="dout">{days_out(r)}d out</span></td>
        <td class="price">${cur:.0f}</td>
        <td class="low">${low:.0f} {delta_html}</td>
        <td class="conf">{conf_html}</td>
        <td class="trend">{sparkline(r)}</td>
        <td class="book"><a href="{r['link']}" target="_blank" rel="noopener">Book ↗</a></td>
      </tr>""")

    updated = datetime.now().strftime("%b %-d, %Y  %-I:%M %p")
    cheapest = f"${current_price(live[0]):.0f}" if live else "—"

    table = ("".join(rows) if rows else
             '<tr><td colspan="7" class="empty">No priced weekends yet '
             '— the radar is still gathering data. Check back after the next sweep.</td></tr>')

    waiting_note = (f'<p class="waiting">+ {len(waiting)} further-out weekends awaiting '
                    f'price data (free flight data only reaches ~2–3 months out; '
                    f'they fill in as the dates approach).</p>' if waiting else "")

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>SFO ⇄ NYC weekend deals</title>
<style>
  :root {{
    --bg:#f6f7f9; --card:#ffffff; --ink:#111418; --muted:#6b7280;
    --line:#e6e8eb; --accent:#0b7285; --up:#c0392b; --down:#2e7d32; --shadow:0 1px 3px rgba(0,0,0,.06);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0e1116; --card:#161b22; --ink:#e6edf3; --muted:#8b949e;
      --line:#232a33; --accent:#39c0d3; --up:#ff6b6b; --down:#4ade80; --shadow:none; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
  .wrap {{ max-width:860px; margin:0 auto; padding:32px 20px 60px; }}
  header h1 {{ font-size:1.5rem; margin:0 0 4px; letter-spacing:-.02em; }}
  header .sub {{ color:var(--muted); font-size:.9rem; margin:0; }}
  .stats {{ display:flex; gap:14px; margin:22px 0 18px; flex-wrap:wrap; }}
  .stat {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
    padding:12px 16px; box-shadow:var(--shadow); flex:1; min-width:130px; }}
  .stat .k {{ color:var(--muted); font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; }}
  .stat .v {{ font-size:1.35rem; font-weight:650; margin-top:2px; }}
  .board {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
    overflow:hidden; box-shadow:var(--shadow); }}
  table {{ width:100%; border-collapse:collapse; }}
  th, td {{ padding:12px 14px; text-align:left; border-bottom:1px solid var(--line); white-space:nowrap; }}
  thead th {{ font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); font-weight:600; }}
  tbody tr:last-child td {{ border-bottom:none; }}
  tbody tr:hover {{ background:color-mix(in srgb, var(--accent) 6%, transparent); }}
  .rank {{ font-size:1.05rem; width:44px; }}
  .wk {{ font-weight:600; }}
  .dout {{ display:block; font-weight:400; font-size:.74rem; color:var(--muted); }}
  .price {{ font-size:1.15rem; font-weight:700; }}
  .delta {{ font-size:.72rem; padding:1px 6px; border-radius:20px; margin-left:4px; }}
  .delta.flat {{ color:var(--down); background:color-mix(in srgb,var(--down) 15%,transparent); }}
  .delta.up {{ color:var(--up); background:color-mix(in srgb,var(--up) 15%,transparent); }}
  .muted {{ color:var(--muted); }}
  .spark.down {{ color:var(--down); }} .spark.up {{ color:var(--up); }}
  .spark-empty {{ color:var(--muted); }}
  .book a {{ color:var(--accent); text-decoration:none; font-weight:600; }}
  .book a:hover {{ text-decoration:underline; }}
  .empty {{ text-align:center; color:var(--muted); padding:30px 14px; white-space:normal; }}
  .waiting {{ color:var(--muted); font-size:.85rem; margin:16px 2px 0; }}
  footer {{ color:var(--muted); font-size:.78rem; margin-top:22px; line-height:1.7; }}
  @media (max-width:560px) {{ .conf, thead .conf-h {{ display:none; }} .trend, thead .trend-h {{ display:none; }} }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>SFO &#8644; NYC — weekend deals</h1>
      <p class="sub">Fri 8pm+ out · land Sun night back · ≤1 stop · any airline, cheapest wins</p>
    </header>

    <div class="stats">
      <div class="stat"><div class="k">Cheapest right now</div><div class="v">{cheapest}</div></div>
      <div class="stat"><div class="k">Weekends priced</div><div class="v">{len(live)}</div></div>
      <div class="stat"><div class="k">Updated</div><div class="v" style="font-size:.95rem">{updated}</div></div>
    </div>

    <div class="board">
      <table>
        <thead><tr>
          <th class="rank">#</th><th>Weekend</th><th>Now</th><th>Low seen</th>
          <th class="conf-h">Confirmed</th><th class="trend-h">Trend</th><th>Link</th>
        </tr></thead>
        <tbody>{table}
        </tbody>
      </table>
    </div>
    {waiting_note}

    <footer>
      <b>Now</b> = coarse radar price (cheapest cached fare, may not match your exact times).
      <b>Confirmed</b> = real Google-Flights price for a schedule-matching flight, set when a new low triggers a check.
      Always click <b>Book</b> to see live times &amp; fares before purchasing.
    </footer>
  </div>
</body>
</html>"""
    with open(OUT, "w") as f:
        f.write(html)
    print(f"wrote {OUT} ({len(live)} priced, {len(waiting)} awaiting)")


if __name__ == "__main__":
    build()
