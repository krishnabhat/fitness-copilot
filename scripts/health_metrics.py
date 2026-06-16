#!/usr/bin/env python3
"""
health_metrics.py — track the athlete's top-value health/fitness metrics and flag
when each is due for re-measurement (like a good coach who keeps you accountable).

Stores a dated history per metric in profile/health_metrics.json and computes
trends, BMI (from weight + height), and a "what's due now" list based on each
metric's recommended cadence.

Usage:
  python3 health_metrics.py --log weight=160 --date 2026-07-01
  python3 health_metrics.py --log vo2max=42
  python3 health_metrics.py --log bp=120/80
  python3 health_metrics.py --summary        # latest values + trend
  python3 health_metrics.py --due            # metrics overdue for re-measurement
  python3 health_metrics.py --set-height 175 # cm, enables BMI

Known metrics (cadence in days): weight 7 · waist 30 · resting_hr 30 · hrv 30 ·
bp 90 · vo2max 90 · mile_time 90 · bodyfat_dexa 180 · bloodwork 180.
Unknown keys are still logged (no cadence/label).
"""

import argparse
import json
import os
from datetime import date, datetime

STORE = os.path.expanduser("~/.claude/skills/fitness-copilot/profile/health_metrics.json")

# key: (label, unit, cadence_days)
KNOWN = {
    "weight": ("Bodyweight", "lb", 7),
    "waist": ("Waist", "in", 30),
    "resting_hr": ("Resting HR", "bpm", 30),
    "hrv": ("HRV", "ms", 30),
    "bp": ("Blood pressure", "mmHg", 90),
    "vo2max": ("VO2max", "ml/kg/min", 90),
    "mile_time": ("1-mile time", "min", 90),
    "bodyfat_dexa": ("Body fat (DEXA)", "%", 180),
    "bloodwork": ("Bloodwork / labs", "panel", 180),
}


def load():
    if os.path.exists(STORE):
        try:
            with open(STORE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"height_cm": None, "metrics": {}}


def save(data):
    with open(STORE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(STORE, 0o600)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def log_metric(data, key, value, when):
    series = data["metrics"].setdefault(key, [])
    series.append({"date": when, "value": value})
    series.sort(key=lambda e: e["date"])
    print(f"logged {key} = {value} @ {when}")


def bmi(data):
    h = data.get("height_cm")
    w = data["metrics"].get("weight")
    if not h or not w:
        return None
    last = _num(w[-1]["value"])
    if last is None:
        return None
    kg = last * 0.453592
    return round(kg / ((h / 100) ** 2), 1)


def summary(data):
    print("=== Health & fitness metrics ===")
    h = data.get("height_cm")
    if h:
        b = bmi(data)
        print(f"Height {h} cm" + (f" · BMI {b}" if b else ""))
    metrics = data.get("metrics", {})
    if not metrics:
        print("(nothing logged yet)")
        return
    for key, series in metrics.items():
        label = KNOWN.get(key, (key, "", None))[0]
        unit = KNOWN.get(key, (key, "", None))[1]
        latest = series[-1]
        line = f"• {label}: {latest['value']}{(' ' + unit) if unit else ''} ({latest['date']})"
        if len(series) >= 2:
            a, b_ = _num(series[0]["value"]), _num(latest["value"])
            if a is not None and b_ is not None and a != 0:
                delta = b_ - a
                line += f"   since {series[0]['date']}: {delta:+.1f} ({(delta/a*100):+.0f}%)"
        print(line)


def due(data, verbose=True):
    today = date.today()
    overdue = []
    metrics = data.get("metrics", {})
    for key, (label, unit, cadence) in KNOWN.items():
        if cadence is None:
            continue
        series = metrics.get(key)
        if not series:
            overdue.append((label, None, cadence))
            continue
        last = datetime.fromisoformat(series[-1]["date"]).date()
        age = (today - last).days
        if age >= cadence:
            overdue.append((label, age, cadence))
    if verbose:
        if not overdue:
            print("✅ All tracked metrics are up to date.")
        else:
            print("📋 Time to measure / update:")
            for label, age, cadence in overdue:
                if age is None:
                    print(f"   • {label} — never logged (recommended every {cadence}d)")
                else:
                    print(f"   • {label} — last {age}d ago (every {cadence}d)")
    return overdue


def main():
    p = argparse.ArgumentParser(description="Track health/fitness metrics.")
    p.add_argument("--log", metavar="KEY=VALUE", help="log a metric, e.g. vo2max=42 or bp=120/80")
    p.add_argument("--date", help="date for --log (YYYY-MM-DD, default today)")
    p.add_argument("--set-height", type=float, metavar="CM", help="set height in cm (enables BMI)")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--due", action="store_true")
    args = p.parse_args()

    data = load()
    changed = False

    if args.set_height:
        data["height_cm"] = args.set_height
        changed = True
        print(f"height set to {args.set_height} cm")
    if args.log:
        if "=" not in args.log:
            p.error("--log must be KEY=VALUE")
        key, value = args.log.split("=", 1)
        when = args.date or date.today().isoformat()
        log_metric(data, key.strip(), value.strip(), when)
        changed = True
    if changed:
        save(data)
    if args.summary:
        summary(data)
    if args.due:
        due(data)
    if not (args.log or args.set_height or args.summary or args.due):
        summary(data)
        print()
        due(data)


if __name__ == "__main__":
    main()
