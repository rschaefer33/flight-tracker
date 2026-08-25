#!/usr/bin/env python3
"""
Weekend flight tracker  —  SF <-> NYC, both directions.

  RADAR      Travelpayouts v3 (free, high volume): coarse cheapest-fare tripwire
             per weekend/direction. Cheap signal for "something moved."
  CONFIRMER  SerpApi Google Flights, two-phase (free ~100/mo): on a radar low it
             pulls the REAL schedule-matching round trip — actual times, airlines,
             layover airports + durations, total price, and a Google Flights link.
  ALERT      ntfy.sh push on a confirmed new low.

Two trips are tracked:
  forward  SF -> NY : out SFO->NYC Fri 8pm+, back NYC->SFO Sun night
  reverse  NY -> SF : out NYC->SFO Fri 8pm+, back SFO->NYC Sun night

All prices USD. Settings live in CONFIG.
"""

import os
import json
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, date

# --------------------------------------------------------------------------- #
NY = ["JFK", "LGA", "EWR"]

CONFIG = {
    "weeks_ahead": 52,
    "currency": "usd",
    "max_stops": 1,
    "out_depart_after": "20:00",   # leave Fri at/after 8 PM
    "out_arrive_by_next": "10:00",  # a 1-stop must land by early Sat AM
    "ret_depart_after": "12:00",   # don't leave before noon Sun (no early flights)
    "ret_arrive_after": "15:00",   # land home at/after 3 PM Sun
    "ret_arrive_by_mon": "02:00",  # ...no later than ~1-2 AM Mon
    "target_price": 500,           # alert when a REAL round trip drops to/under this
    "max_confirm_events_per_run": 3,   # each event = 2 SerpApi calls (per-run guard)
    "daily_confirm_events": 5,         # ...and a daily cap so hourly runs stay <250/mo
    "populate_until": "2027-02-28",    # --populate confirms weekends through this date
}

# trip -> which airports are the origin/destination of the OUTBOUND leg
TRIPS = {
    "forward": {"label": "SF → New York", "out": (["SFO"], NY)},
    "reverse": {"label": "New York → SF", "out": (NY, ["SFO"])},
}

TP_TOKEN = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY_PATH = os.path.join(HERE, "history.json")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _hhmm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _mins(dt):
    return dt.hour * 60 + dt.minute


def parse_dt(s):
    s = s.strip()
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError:
        return datetime.strptime(s[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=None)


def upcoming_weekends(n):
    today = date.today()
    days = (4 - today.weekday()) % 7
    first = today + timedelta(days=days or 7)
    for i in range(n):
        fri = first + timedelta(weeks=i)
        yield fri, fri + timedelta(days=2)


def gflink(dep_ids, arr_ids, fri, sun):
    origin = "SFO" if dep_ids == ["SFO"] else "New York"
    dest = "SFO" if arr_ids == ["SFO"] else "New York"
    q = f"flights from {origin} to {dest} on {fri.isoformat()} returning {sun.isoformat()}"
    return "https://www.google.com/travel/flights?q=" + urllib.parse.quote(q)


def _get_json(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        return None, f"{e.code} {e.read().decode(errors='ignore')[:160]}"
    except urllib.error.URLError as e:
        return None, str(e)


def notify(title, body):
    if not NTFY_TOPIC:
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}", data=body.encode("utf-8"),
        headers={"Title": title.encode("ascii", "ignore").decode(),
                 "Priority": "high", "Tags": "airplane,money_with_wings"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print(f"    ! notify failed: {e}")


# --------------------------------------------------------------------------- #
# RADAR  (Travelpayouts, free)
# --------------------------------------------------------------------------- #
def _tp_cheapest(origin, destination, day):
    params = {"origin": origin, "destination": destination,
              "departure_at": day.isoformat(), "currency": CONFIG["currency"],
              "one_way": "true", "sorting": "price", "limit": 30,
              "market": "us", "token": TP_TOKEN}
    url = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates?" + urllib.parse.urlencode(params)
    data, err = _get_json(url)
    if err:
        print(f"    ! radar {origin}->{destination} {day}: {err}")
        return None
    rows = (data or {}).get("data", [])
    pref = [r for r in rows if r.get("transfers", 9) <= CONFIG["max_stops"]] or rows
    return float(min(pref, key=lambda r: float(r["price"]))["price"]) if pref else None


def radar(trip_key, fri, sun):
    """Coarse round-trip tripwire = cheapest one-way out + cheapest one-way back."""
    if not TP_TOKEN:
        raise SystemExit("Missing TRAVELPAYOUTS_TOKEN (see README).")
    if trip_key == "forward":
        o = _tp_cheapest("SFO", "NYC", fri); r = _tp_cheapest("NYC", "SFO", sun)
    else:
        o = _tp_cheapest("NYC", "SFO", fri); r = _tp_cheapest("SFO", "NYC", sun)
    return (o + r) if (o and r) else None


# --------------------------------------------------------------------------- #
# CONFIRMER  (SerpApi Google Flights, two-phase)
# --------------------------------------------------------------------------- #
def _serp(extra):
    params = {"engine": "google_flights", "currency": "USD", "hl": "en",
              "gl": "us", "api_key": SERPAPI_KEY, **extra}
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    data, err = _get_json(url)
    if err:
        print(f"    ! serp: {err}")
        return None
    return data


def _stops(opt):
    return len(opt["flights"]) - 1


def _qual_out(opt, day, dep_ids, arr_ids):
    if _stops(opt) > CONFIG["max_stops"]:
        return False
    segs = opt["flights"]
    dep = parse_dt(segs[0]["departure_airport"]["time"])
    arr = parse_dt(segs[-1]["arrival_airport"]["time"])
    if segs[0]["departure_airport"]["id"] not in dep_ids:
        return False
    if segs[-1]["arrival_airport"]["id"] not in arr_ids:
        return False
    if dep.date() != day or _mins(dep) < _hhmm(CONFIG["out_depart_after"]):
        return False
    if _stops(opt) == 1:
        nxt = day + timedelta(days=1)
        if not (arr.date() == nxt and _mins(arr) <= _hhmm(CONFIG["out_arrive_by_next"])):
            return False
    return True


def _qual_ret(opt, day, dep_ids, arr_ids):
    if _stops(opt) > CONFIG["max_stops"]:
        return False
    segs = opt["flights"]
    dep = parse_dt(segs[0]["departure_airport"]["time"])
    arr = parse_dt(segs[-1]["arrival_airport"]["time"])
    if segs[0]["departure_airport"]["id"] not in dep_ids:
        return False
    if segs[-1]["arrival_airport"]["id"] not in arr_ids:
        return False
    if dep.date() != day or _mins(dep) < _hhmm(CONFIG["ret_depart_after"]):
        return False
    mon = day + timedelta(days=1)
    return ((arr.date() == day and _mins(arr) >= _hhmm(CONFIG["ret_arrive_after"]))
            or (arr.date() == mon and _mins(arr) <= _hhmm(CONFIG["ret_arrive_by_mon"])))


def _leg(opt):
    segs = [{"f": s["departure_airport"]["id"], "ft": s["departure_airport"]["time"],
             "t": s["arrival_airport"]["id"], "tt": s["arrival_airport"]["time"],
             "al": s.get("airline"), "fn": s.get("flight_number")}
            for s in opt["flights"]]
    lay = [{"id": l.get("id"), "dur": l.get("duration")} for l in opt.get("layovers", [])]
    return {"segs": segs, "layovers": lay, "stops": _stops(opt),
            "duration": opt.get("total_duration")}


def confirm(fri, sun, dep_ids, arr_ids):
    """Two SerpApi calls -> real cheapest schedule-matching round trip, or None."""
    if not SERPAPI_KEY:
        return None
    base = {"departure_id": ",".join(dep_ids), "arrival_id": ",".join(arr_ids),
            "outbound_date": fri.isoformat(), "return_date": sun.isoformat()}
    d1 = _serp(base)
    if not d1:
        return None
    outs = (d1.get("best_flights") or []) + (d1.get("other_flights") or [])
    qo = sorted([o for o in outs if _qual_out(o, fri, dep_ids, arr_ids)],
                key=lambda o: o["price"])
    if not qo or not qo[0].get("departure_token"):
        return None
    out = qo[0]
    d2 = _serp({**base, "departure_token": out["departure_token"]})
    if not d2:
        return None
    rets = (d2.get("best_flights") or []) + (d2.get("other_flights") or [])
    qr = sorted([o for o in rets if _qual_ret(o, sun, arr_ids, dep_ids)],
                key=lambda o: o["price"])
    if not qr:
        return None
    ret = qr[0]
    return {"price": float(ret["price"]),
            "checked": datetime.now().isoformat(timespec="seconds"),
            "out": _leg(out), "ret": _leg(ret),
            "link": gflink(dep_ids, arr_ids, fri, sun)}


# --------------------------------------------------------------------------- #
# history + run
# --------------------------------------------------------------------------- #
def load_history():
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH) as f:
            h = json.load(f)
        if "forward" in h or "reverse" in h:      # already nested
            return {"forward": h.get("forward", {}), "reverse": h.get("reverse", {}),
                    "_meta": h.get("_meta", {"date": "", "events": 0})}
    return {"forward": {}, "reverse": {}, "_meta": {"date": "", "events": 0}}


def save_history(h):
    with open(HISTORY_PATH, "w") as f:
        json.dump(h, f, indent=2)


def _apply_confirm(rec, label, key, fri, sun, dep_ids, arr_ids, target, populate, alerts):
    """Run a confirm and fold the result into rec; queue an alert if warranted."""
    c = confirm(fri, sun, dep_ids, arr_ids)
    if not c:
        return
    cprev = rec["confirmed_low"]
    rec["confirmed"] = c
    is_low = cprev is None or c["price"] < cprev
    if is_low:
        rec["confirmed_low"] = c["price"]
    crossed = (target is not None and c["price"] <= target and (cprev is None or cprev > target))
    if not populate and ((is_low and cprev is not None) or crossed):
        alerts.append((label, key, cprev, c))


def run(populate=False):
    history = load_history()
    now = datetime.now().isoformat(timespec="seconds")
    target = CONFIG["target_price"]
    cutoff = date.fromisoformat(CONFIG["populate_until"])
    alerts = []

    # daily SerpApi budget so hourly sweeps can't blow the monthly free cap
    meta = history.setdefault("_meta", {"date": "", "events": 0})
    today = date.today().isoformat()
    if meta["date"] != today:
        meta.update(date=today, events=0)
    if populate:
        budget = 60
    else:
        budget = max(0, min(CONFIG["max_confirm_events_per_run"],
                            CONFIG["daily_confirm_events"] - meta["events"]))

    # PASS 1 — radar sweep (free); collect confirm candidates
    allw, new_lows = [], []
    for trip_key, spec in TRIPS.items():
        dep_ids, arr_ids = spec["out"]
        book = history.setdefault(trip_key, {})
        for fri, sun in upcoming_weekends(CONFIG["weeks_ahead"]):
            key = fri.isoformat()
            rec = book.setdefault(key, {"friday": key, "sunday": sun.isoformat(),
                                        "radar_low": None, "samples": [],
                                        "confirmed": None, "confirmed_low": None})
            entry = (spec["label"], key, rec, fri, sun, dep_ids, arr_ids)
            allw.append(entry)
            price = radar(trip_key, fri, sun)
            if price is not None:
                rec["samples"].append({"t": now, "radar": price})
                prev = rec["radar_low"]
                if prev is None or price < prev:
                    rec["radar_low"] = price
                    if prev is not None:      # a genuine downward move
                        new_lows.append(entry)
            if populate and fri <= cutoff:
                new_lows.append(entry)

    # PASS 2 — spend budget: new lows / populate first
    done = set()
    for label, key, rec, fri, sun, dep_ids, arr_ids in new_lows:
        if budget <= 0:
            break
        if (label, key) in done:
            continue
        _apply_confirm(rec, label, key, fri, sun, dep_ids, arr_ids, target, populate, alerts)
        done.add((label, key)); budget -= 1; meta["events"] += 1
        time.sleep(0.4)

    # PASS 3 — rotation: keep the stalest confirmed weekends fresh with leftover budget
    if not populate and budget > 0:
        conf = [e for e in allw if e[2].get("confirmed") and (e[0], e[1]) not in done]
        conf.sort(key=lambda e: e[2]["confirmed"].get("checked", ""))
        for label, key, rec, fri, sun, dep_ids, arr_ids in conf:
            if budget <= 0:
                break
            _apply_confirm(rec, label, key, fri, sun, dep_ids, arr_ids, target, populate, alerts)
            budget -= 1; meta["events"] += 1
            time.sleep(0.4)

    n_live = sum(1 for e in allw if e[2].get("confirmed"))
    print(f"swept {len(allw)} weekend-slots · {n_live} live itineraries · "
          f"confirms today: {meta['events']}")
    save_history(history)

    if alerts:
        print("\n=== ALERTS ===")
        lines = []
        for label, key, prev, c in alerts:
            dt = datetime.fromisoformat(key)
            was = f"${prev:.0f} -> " if prev else ""
            stops = "nonstop" if c["out"]["stops"] == 0 and c["ret"]["stops"] == 0 else "connect"
            print(f"  [{label}] {key}: {was}${c['price']:.0f} ({stops})")
            lines.append(f"{label} — {dt:%a %b %d}: {was}${c['price']:.0f} ({stops})\n{c['link']}")
        notify(f"Flight low: ${min(c['price'] for *_, c in alerts):.0f}",
               "\n\n".join(lines))
    else:
        print("\nNo confirmed new lows this sweep.")
    return alerts


if __name__ == "__main__":
    import sys
    run(populate="--populate" in sys.argv)
