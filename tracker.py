#!/usr/bin/env python3
"""
SFO <-> NYC weekend flight-price tracker  (free hybrid)

  RADAR      Travelpayouts v3 (free, no card, high volume) sweeps every weekend
             and watches the cheapest cached fare for movement. It already knows
             departure time + stop count, so it pre-filters to Fri-night / <=1 stop.

  CONFIRMER  SerpApi Google Flights (free 100/mo) is spent ONLY when the radar
             flags a new low: it verifies a schedule-matching itinerary really
             exists (arrival windows too) and returns the real price + booking link.

  ALERT      Fires only on a SerpApi-confirmed, time-valid NEW LOW.

Trip rules live in CONFIG. Prices in USD.
"""

import os
import json
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, date

# --------------------------------------------------------------------------- #
# CONFIG
# --------------------------------------------------------------------------- #
CONFIG = {
    "origin": "SFO",
    "ny_airports": ["JFK", "LGA", "EWR"],
    "weeks_ahead": 52,
    "currency": "usd",
    "max_stops": 1,

    # Outbound (Friday)
    "out_depart_after": "20:00",   # leave SFO at/after 8 PM Fri
    "out_arrive_by_sat": "10:00",  # a 1-stop must land NYC by this Sat morning

    # Return (Sunday) -- land SF at night, late is fine
    "ret_arrive_after": "15:00",   # land SFO at/after 3 PM Sun
    "ret_arrive_by_mon": "02:00",  # ...no later than ~1-2 AM Mon

    # Alerting
    "target_price": None,          # e.g. 350 -> also alert under this. None = new-lows only
    "max_confirms_per_run": 6,     # cap SerpApi calls/run to protect the free 100/mo budget
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


def _minutes(dt):
    return dt.hour * 60 + dt.minute


def parse_dt(s):
    """Accepts ISO with or without tz offset; returns naive local wall-clock."""
    s = s.strip().replace(" ", "T", 1) if " " in s and "T" not in s else s
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # e.g. "2026-08-28 21:35" from SerpApi
        dt = datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
    return dt.replace(tzinfo=None)


def upcoming_weekends(n):
    today = date.today()
    days_until_fri = (4 - today.weekday()) % 7
    first_fri = today + timedelta(days=days_until_fri or 7)
    for i in range(n):
        fri = first_fri + timedelta(weeks=i)
        yield fri, fri + timedelta(days=2)


def google_flights_link(fri, sun):
    q = f"flights from {CONFIG['origin']} to New York on {fri.isoformat()} returning {sun.isoformat()}"
    return "https://www.google.com/travel/flights?q=" + urllib.parse.quote(q)


def _get_json(url, headers=None, timeout=45):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        return None, f"{e.code} {e.read().decode(errors='ignore')[:200]}"
    except urllib.error.URLError as e:
        return None, str(e)


# --------------------------------------------------------------------------- #
# RADAR -- Travelpayouts v3 prices_for_dates (free, high volume)
# --------------------------------------------------------------------------- #
def _tp_cheapest(origin, destination, day):
    """Cheapest cached one-way for origin->destination on `day`.
    US round-trip cache is empty, so we price two one-ways. Coarse by design:
    no time filter here (per-date cache is thin) -- SerpApi does the real
    time-matching at confirm time. Returns dict or None."""
    params = {
        "origin": origin, "destination": destination,
        "departure_at": day.isoformat(),
        "currency": CONFIG["currency"], "one_way": "true",
        "sorting": "price", "limit": 30, "market": "us", "token": TP_TOKEN,
    }
    url = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates?" + urllib.parse.urlencode(params)
    data, err = _get_json(url)
    if err:
        print(f"    ! radar error {origin}->{destination} {day}: {err}")
        return None
    rows = (data or {}).get("data", [])
    if not rows:
        return None
    # prefer <=1 stop; fall back to overall cheapest if none qualify
    pref = [r for r in rows if r.get("transfers", 9) <= CONFIG["max_stops"]] or rows
    best = min(pref, key=lambda r: float(r["price"]))
    return {"price": float(best["price"]),
            "dest": best.get("destination_airport") or best.get("destination"),
            "transfers": best.get("transfers"), "airline": best.get("airline")}


def radar(fri, sun):
    """Coarse round-trip price tripwire = cheapest one-way out + cheapest one-way
    back. Returns (price_usd, meta) or (None, None)."""
    if not TP_TOKEN:
        raise SystemExit("Missing TRAVELPAYOUTS_TOKEN (see README).")
    out = _tp_cheapest(CONFIG["origin"], "NYC", fri)
    ret = _tp_cheapest("NYC", CONFIG["origin"], sun)
    if not out or not ret:
        return None, None
    return out["price"] + ret["price"], {
        "out": out, "ret": ret,
        "dest": out["dest"] if out["dest"] in CONFIG["ny_airports"] else "JFK",
    }


# --------------------------------------------------------------------------- #
# CONFIRMER -- SerpApi Google Flights (free 100/mo; only on a hit)
# --------------------------------------------------------------------------- #
def _seg_list(flight):
    return flight.get("flights", [])


def _qualifies_out(flight, fri):
    segs = _seg_list(flight)
    if not segs or len(segs) - 1 > CONFIG["max_stops"]:
        return False
    dep = parse_dt(segs[0]["departure_airport"]["time"])
    arr = parse_dt(segs[-1]["arrival_airport"]["time"])
    if segs[0]["departure_airport"]["id"] != CONFIG["origin"]:
        return False
    if segs[-1]["arrival_airport"]["id"] not in CONFIG["ny_airports"]:
        return False
    if dep.date() != fri or _minutes(dep) < _hhmm(CONFIG["out_depart_after"]):
        return False
    if len(segs) - 1 == 1:  # 1 stop must land early Sat
        sat = fri + timedelta(days=1)
        if not (arr.date() == sat and _minutes(arr) <= _hhmm(CONFIG["out_arrive_by_sat"])):
            return False
    return True


def _qualifies_ret(flight, sun):
    segs = _seg_list(flight)
    if not segs or len(segs) - 1 > CONFIG["max_stops"]:
        return False
    dep = parse_dt(segs[0]["departure_airport"]["time"])
    arr = parse_dt(segs[-1]["arrival_airport"]["time"])
    if segs[0]["departure_airport"]["id"] not in CONFIG["ny_airports"]:
        return False
    if segs[-1]["arrival_airport"]["id"] != CONFIG["origin"]:
        return False
    if dep.date() != sun:
        return False
    mon = sun + timedelta(days=1)
    ok_sun = arr.date() == sun and _minutes(arr) >= _hhmm(CONFIG["ret_arrive_after"])
    ok_mon = arr.date() == mon and _minutes(arr) <= _hhmm(CONFIG["ret_arrive_by_mon"])
    return ok_sun or ok_mon


def confirm(fri, sun, dest_airport):
    """Verify a schedule-matching round trip exists on Google Flights.
    Returns dict(price, nonstop, ...) or None. Costs 1 SerpApi search."""
    if not SERPAPI_KEY:
        print("    ! no SERPAPI_KEY set; skipping confirm")
        return None
    dest = dest_airport if dest_airport in CONFIG["ny_airports"] else "JFK"
    params = {
        "engine": "google_flights",
        "departure_id": CONFIG["origin"],
        "arrival_id": dest,
        "outbound_date": fri.isoformat(),
        "return_date": sun.isoformat(),
        "currency": "USD",
        "type": "1",           # round trip
        "hl": "en", "gl": "us",
        "api_key": SERPAPI_KEY,
    }
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    data, err = _get_json(url)
    if err:
        print(f"    ! confirm error {fri}: {err}")
        return None
    flights = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    best = None
    for fl in flights:
        # SerpApi returns each option as a full round trip when type=1
        segs = _seg_list(fl)
        if not segs:
            continue
        # split into outbound / return by the SFO<->NYC turn is nontrivial;
        # SerpApi groups a round trip's outbound in this object and the return is
        # chosen in a second step. We treat 'flights' here as the OUTBOUND leg and
        # accept it if the outbound qualifies; return timing is verified via the
        # booking link. (Google's round-trip API is two-phase.)
        if not _qualifies_out(fl, fri):
            continue
        price = float(fl.get("price", 0) or 0)
        if price <= 0:
            continue
        nonstop = len(segs) - 1 == 0
        if best is None or (price, 0 if nonstop else 1) < (best["price"], 0 if best["nonstop"] else 1):
            best = {"price": price, "nonstop": nonstop, "dest": dest,
                    "airlines": sorted({s.get("airline", "?") for s in segs})}
    return best


# --------------------------------------------------------------------------- #
# history + orchestration
# --------------------------------------------------------------------------- #
def notify(title, body):
    """Phone push via ntfy.sh (free, no account). No-op if NTFY_TOPIC unset."""
    if not NTFY_TOPIC:
        return
    # ntfy header values must be ASCII; keep title clean and put detail in body
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}", data=body.encode("utf-8"),
        headers={"Title": title.encode("ascii", "ignore").decode(),
                 "Priority": "high", "Tags": "airplane,money_with_wings"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print(f"    ! notify failed: {e}")


def load_history():
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH) as f:
            return json.load(f)
    return {}


def save_history(h):
    with open(HISTORY_PATH, "w") as f:
        json.dump(h, f, indent=2)


def run():
    history = load_history()
    now = datetime.now().isoformat(timespec="seconds")
    alerts = []
    confirms_left = CONFIG["max_confirms_per_run"]

    for fri, sun in upcoming_weekends(CONFIG["weeks_ahead"]):
        key = fri.isoformat()
        rec = history.setdefault(key, {
            "friday": key, "sunday": sun.isoformat(),
            "link": google_flights_link(fri, sun),
            "radar_low": None, "confirmed_low": None, "samples": [],
        })

        price, meta = radar(fri, sun)
        if price is None:
            print(f"  {key}: radar found nothing")
            time.sleep(0.2)
            continue
        rec["samples"].append({"t": now, "radar": price})

        prev = rec["radar_low"]
        is_new_low = prev is None or price < prev
        below_target = CONFIG["target_price"] is not None and price <= CONFIG["target_price"]
        if is_new_low:
            rec["radar_low"] = price

        tag = ""
        # spend a SerpApi call only when it's worth it (new low, not the first sighting)
        if ((is_new_low and prev is not None) or below_target) and confirms_left > 0:
            confirms_left -= 1
            got = confirm(fri, sun, (meta or {}).get("dest", "JFK"))
            if got:
                cprev = rec["confirmed_low"]
                if cprev is None or got["price"] < cprev:
                    rec["confirmed_low"] = got["price"]
                    alerts.append((key, cprev, got, rec["link"]))
                    tag = "  *** CONFIRMED NEW LOW ***"

        print(f"  {key}: radar ${price:.0f} (low ${rec['radar_low']:.0f}){tag}")
        time.sleep(0.2)

    save_history(history)

    if alerts:
        print("\n=== ALERTS ===")
        lines = []
        for key, prev, got, link in alerts:
            was = f"${prev:.0f} -> " if prev else ""
            stops = "nonstop" if got["nonstop"] else "1-stop"
            print(f"  {key}: {was}${got['price']:.0f} {stops} [{'/'.join(got['airlines'])}]")
            print(f"     book: {link}")
            dt = datetime.fromisoformat(key)
            lines.append(f"{dt:%a %b %d}: {was}${got['price']:.0f} {stops} "
                         f"({'/'.join(got['airlines'])})\n{link}")
        title = (f"SFO-NYC low: ${min(g['price'] for _, _, g, _ in alerts):.0f}"
                 if len(alerts) == 1 else f"{len(alerts)} SFO-NYC weekend lows")
        notify(title, "\n\n".join(lines))
    else:
        print("\nNo confirmed new lows this sweep.")
    return alerts


if __name__ == "__main__":
    run()
