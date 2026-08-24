# SFO ⇄ NYC weekend flight tracker (free hybrid)

Watches every upcoming weekend for the cheapest qualifying round trip and alerts
on **confirmed new lows**, with a Google Flights link to book.

## How it works
- **Radar — Travelpayouts** (free, no card, high volume): sweeps all weekends
  often. Already knows departure time + stops, so it pre-filters to Fri-night /
  ≤1-stop and watches the cheapest fare for movement.
- **Confirmer — SerpApi Google Flights** (free 100/mo): spent *only* when the
  radar flags a new low — verifies a schedule-matching itinerary exists and
  returns the real price + booking link.
- **Alert** fires only on a confirmed, time-valid new low.

## Trip rules  (edit the `CONFIG` block in `tracker.py`)
- **Out:** SFO → NYC (JFK/LGA/EWR), depart **Fri ≥ 8:00 PM**. Nonstop preferred;
  1-stop OK only if it lands NYC by early Sat AM.
- **Back:** NYC → SFO, depart **Sun**, land SFO **Sun night** (up to ~1 AM Mon).
- Any airline. Cheapest wins; nonstop is a tie-break.
- `target_price` is `None` (new-lows only). Set it later to also alert under $X.

## Setup (one time — both free, no credit card)
1. **Travelpayouts:** register at https://www.travelpayouts.com → Profile →
   **API token** tab → copy it.
2. **SerpApi:** register at https://serpapi.com → API key shows on the dashboard.
3. `cp .env.example .env` and paste both values in.
4. Run:
   ```bash
   ./run.sh
   ```

First run records baselines; later runs print `*** CONFIRMED NEW LOW ***` + the
booking link. History is stored in `history.json`.

## Known refinement (v1)
SerpApi round-trip search is two-phase; right now `confirm()` validates the
**outbound** leg precisely and leans on the radar's return filter + the booking
link for the return. A later pass can add the second SerpApi call (via
`departure_token`) to verify return arrival time end-to-end.

## Live deployment
- **Runs in the cloud** via GitHub Actions (`.github/workflows/track.yml`), every
  3 hours, no laptop needed. Manual run: Actions tab → "flight-tracker" → Run, or
  `gh workflow run track.yml`.
- **Price history** is committed back to the repo each run (`history.json`).
- **Phone alerts** via ntfy.sh. Secrets live in GitHub (Settings → Secrets):
  `TRAVELPAYOUTS_TOKEN`, `SERPAPI_KEY`, `NTFY_TOPIC`.

### Get the alerts on your phone
Install the **ntfy** app (iOS/Android) → add subscription → server `ntfy.sh`,
topic = the value of `NTFY_TOPIC` (kept locally in `.ntfy_topic.txt`, not in git).

## Changing settings later
Edit `CONFIG` in `tracker.py`, commit, push — the next run uses it.
- `target_price`: set a number to also alert under $X (currently new-lows only).
- times / stops / weeks_ahead: all in `CONFIG`.
- sweep frequency: the `cron` line in `.github/workflows/track.yml`.
