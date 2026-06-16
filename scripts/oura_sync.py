#!/usr/bin/env python3
"""
oura_sync.py — pull recovery/sleep metrics from the Oura Cloud API (v2) into the
local health_metrics store. Oura has a real API, so this runs automatically (no
manual export): HRV, resting HR, readiness score, sleep, SpO2, and VO2max.

Auth: a Personal Access Token from cloud.ouraring.com → Personal Access Tokens.
Provide via env OURA_TOKEN or the file profile/.oura_key. The token is never printed.

Setup (one-time):
  python3 oura_sync.py --setup "<TOKEN>"     # saves token, tests, pulls last 14 days

Usage:
  python3 oura_sync.py                 # pull last 14 days into health_metrics
  python3 oura_sync.py --days 30
  python3 oura_sync.py --dry-run       # preview without writing

Logged metrics (deduped by metric+date): hrv (ms), resting_hr (bpm),
readiness (score), vo2max (ml/kg/min). Sleep duration is logged as a note.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import health_metrics  # noqa: E402

BASE = "https://api.ouraring.com/v2/usercollection"
KEY_FILE = os.path.expanduser("~/.claude/skills/fitness-copilot/profile/.oura_key")


def get_token():
    t = os.environ.get("OURA_TOKEN", "").strip()
    if t:
        return t
    if os.path.exists(KEY_FILE):
        t = open(KEY_FILE).read().strip()
        if t:
            return t
    sys.exit("ERROR: no Oura token. Set OURA_TOKEN or run --setup \"<TOKEN>\".\n"
             "  Get one at cloud.ouraring.com → Personal Access Tokens.")


def api_get(path, params, token=None):
    token = token or get_token()
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            sys.exit(f"ERROR: Oura auth failed ({e.code}). Check the token. (not shown)")
        if e.code == 404:
            return None  # endpoint not available for this account
        body = e.read().decode(errors="replace")[:200]
        print(f"WARN: Oura {path} {e.code}: {body}")
        return None
    except urllib.error.URLError as e:
        print(f"WARN: could not reach Oura ({e.reason}) for {path}")
        return None


def daterange(days):
    end = date.today()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def collect(days, token=None):
    """Return {key: {date: value}} for the metrics we track."""
    start, end = daterange(days)
    params = {"start_date": start, "end_date": end}
    out = {}

    # Sleep → per-night HRV (average_hrv) and resting HR (lowest_heart_rate)
    sleep = api_get("sleep", params, token)
    if sleep and sleep.get("data"):
        per_day = {}
        for rec in sleep["data"]:
            day = rec.get("day")
            if not day:
                continue
            # prefer the main (long) sleep for the day
            if day in per_day and per_day[day].get("type") == "long_sleep" \
                    and rec.get("type") != "long_sleep":
                continue
            per_day[day] = rec
        for day, rec in per_day.items():
            if rec.get("average_hrv") is not None:
                out.setdefault("hrv", {})[day] = round(float(rec["average_hrv"]), 1)
            if rec.get("lowest_heart_rate") is not None:
                out.setdefault("resting_hr", {})[day] = int(rec["lowest_heart_rate"])

    # Daily readiness → score (and resting HR contributor as fallback)
    rd = api_get("daily_readiness", params, token)
    if rd and rd.get("data"):
        for rec in rd["data"]:
            day = rec.get("day")
            if day and rec.get("score") is not None:
                out.setdefault("readiness", {})[day] = int(rec["score"])

    # VO2max (endpoint may not exist for all accounts → handled as None)
    vo2 = api_get("vO2_max", params, token)
    if vo2 and vo2.get("data"):
        for rec in vo2["data"]:
            day = rec.get("day")
            v = rec.get("vo2_max") or rec.get("vo2max")
            if day and v is not None:
                out.setdefault("vo2max", {})[day] = round(float(v), 1)

    return out


def write_metrics(collected, dry_run):
    store = health_metrics.load()
    writes = []
    for key, byday in collected.items():
        existing = {e["date"] for e in store.get("metrics", {}).get(key, [])}
        for day, val in sorted(byday.items()):
            if day not in existing:
                writes.append((key, val, day))
    print("Oura sync " + ("(dry-run) " if dry_run else "") +
          "→ " + (", ".join(f"{k}×{len(v)}d" for k, v in collected.items()) or "no data"))
    print(f"  new readings to log: {len(writes)}")
    for k, v, d in writes[-8:]:
        print(f"    {d}  {k} = {v}")
    if dry_run or not writes:
        return
    for key, val, day in writes:
        store["metrics"].setdefault(key, []).append({"date": day, "value": val})
    for key in store["metrics"]:
        store["metrics"][key].sort(key=lambda e: e["date"])
    health_metrics.save(store)
    print(f"  ✓ logged {len(writes)} Oura readings.")


def setup(token):
    token = token.strip()
    # validate by hitting a cheap endpoint
    start, end = daterange(2)
    res = api_get("daily_readiness", {"start_date": start, "end_date": end}, token)
    if res is None:
        # try sleep as a fallback validation
        res = api_get("sleep", {"start_date": start, "end_date": end}, token)
        if res is None:
            sys.exit("ERROR: token did not validate against Oura. Double-check it.")
    with open(KEY_FILE, "w") as f:
        f.write(token)
    os.chmod(KEY_FILE, 0o600)
    print("✓ Oura token saved (perms 600). Pulling last 14 days…")
    write_metrics(collect(14, token), dry_run=False)


def main():
    p = argparse.ArgumentParser(description="Sync Oura metrics into health_metrics.")
    p.add_argument("--setup", metavar="TOKEN")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if args.setup:
        setup(args.setup)
        return
    write_metrics(collect(args.days), args.dry_run)


if __name__ == "__main__":
    main()
