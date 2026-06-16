#!/usr/bin/env python3
"""
applehealth_import.py — import an Apple Health export into the local log + metrics.

Apple Health has no cloud API, so this reads a manual export:
  iPhone Health app -> profile photo -> "Export All Health Data" -> AirDrop the
  export.zip to your Mac, then:
    python3 applehealth_import.py ~/Downloads/export.zip
    python3 applehealth_import.py ~/Downloads/export.zip --dry-run   # preview only
    python3 applehealth_import.py export.xml --since 2026-05-01

Pulls the latest value per day for HRV (SDNN), resting HR, VO2max, bodyweight,
and body-fat %, into profile/health_metrics.json (deduped by metric+date), and
imports Workouts into the activity log (source=apple_health, deduped). Uses a
streaming XML parser so very large exports don't blow up memory.
"""

import argparse
import os
import sys
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import activity_log    # noqa: E402
import health_metrics  # noqa: E402

# Apple Record type -> our health_metrics key (+ whether to convert kg→lb)
METRIC_TYPES = {
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": ("hrv", False),
    "HKQuantityTypeIdentifierRestingHeartRate": ("resting_hr", False),
    "HKQuantityTypeIdentifierVO2Max": ("vo2max", False),
    "HKQuantityTypeIdentifierBodyMass": ("weight", True),
    "HKQuantityTypeIdentifierBodyFatPercentage": ("bodyfat", False),
}

WORKOUT_MAP = {
    "Running": "run", "Cycling": "bike", "Walking": "walk", "Hiking": "walk",
    "Swimming": "swim", "Yoga": "yoga", "HighIntensityIntervalTraining": "hiit",
    "TraditionalStrengthTraining": "strength", "FunctionalStrengthTraining": "strength",
}


def open_xml(path):
    if path.endswith(".zip"):
        zf = zipfile.ZipFile(path)
        name = next((n for n in zf.namelist() if n.endswith("export.xml")), None)
        if not name:
            sys.exit("ERROR: no export.xml inside the zip.")
        return zf.open(name)
    return open(path, "rb")


def parse(path, since):
    """Return (metrics: {key: {date: (value, raw)}}, workouts: [entry])."""
    metrics = {}
    workouts = []
    fh = open_xml(path)
    for event, el in ET.iterparse(fh, events=("end",)):
        tag = el.tag
        if tag == "Record":
            rtype = el.get("type")
            mk = METRIC_TYPES.get(rtype)
            if mk:
                key, conv = mk
                day = (el.get("startDate") or "")[:10]
                val = el.get("value")
                if day and val and (not since or day >= since):
                    try:
                        num = float(val)
                        if conv and "kg" in (el.get("unit") or "").lower():
                            num = round(num * 2.20462, 1)
                        # Apple stores body-fat as a fraction (0.20 = 20%); show as %.
                        if key == "bodyfat" and num < 1.5:
                            num = round(num * 100, 1)
                        # keep the latest reading per day
                        metrics.setdefault(key, {})[day] = num
                    except ValueError:
                        pass
            el.clear()
        elif tag == "Workout":
            atype_raw = (el.get("workoutActivityType") or "").replace("HKWorkoutActivityType", "")
            atype = WORKOUT_MAP.get(atype_raw, "other")
            start = el.get("startDate") or ""
            day = start[:10]
            if day and (not since or day >= since):
                dur = el.get("duration")
                dist = el.get("totalDistance")
                dunit = (el.get("totalDistanceUnit") or "").lower()
                entry = {
                    "source": "apple_health",
                    "source_id": f"{start}|{atype_raw}",
                    "date": day,
                    "type": atype,
                    "title": f"{atype_raw or 'Workout'} (Apple Health)",
                    "detail": f"Apple Health workout: {atype_raw}",
                }
                if dur:
                    try:
                        entry["duration_min"] = round(float(dur), 1)
                    except ValueError:
                        pass
                if dist:
                    try:
                        km = float(dist)
                        if "mi" in dunit:
                            km *= 1.60934
                        entry["distance_km"] = round(km, 2)
                    except ValueError:
                        pass
                workouts.append(entry)
            el.clear()
    fh.close()
    return metrics, workouts


def main():
    p = argparse.ArgumentParser(description="Import an Apple Health export.")
    p.add_argument("path", help="export.zip or export.xml")
    p.add_argument("--since", help="only import data on/after this date (YYYY-MM-DD)")
    p.add_argument("--only-metrics", help="comma-separated metric keys to import "
                   "(e.g. weight,bodyfat); others skipped. Default: all")
    p.add_argument("--no-workouts", action="store_true", help="skip workout import")
    p.add_argument("--dry-run", action="store_true", help="preview without writing")
    args = p.parse_args()

    if not os.path.exists(args.path):
        sys.exit(f"ERROR: file not found: {args.path}")
    metrics, workouts = parse(args.path, args.since)

    if args.only_metrics:
        keep = {k.strip() for k in args.only_metrics.split(",")}
        metrics = {k: v for k, v in metrics.items() if k in keep}
    if args.no_workouts:
        workouts = []

    # plan metric writes (dedup by key+date against existing store)
    store = health_metrics.load()
    metric_writes = []
    for key, byday in metrics.items():
        existing = {e["date"] for e in store.get("metrics", {}).get(key, [])}
        for day, val in sorted(byday.items()):
            if day not in existing:
                metric_writes.append((key, val, day))

    print(f"Apple Health import {'(dry-run) ' if args.dry_run else ''}from {os.path.basename(args.path)}:")
    print(f"  metrics found: " + ", ".join(f"{k}×{len(v)}d" for k, v in metrics.items()) or "  (none)")
    print(f"  new metric readings to log: {len(metric_writes)}")
    print(f"  workouts found: {len(workouts)}")
    for k, v, d in metric_writes[-6:]:
        print(f"    {d}  {k} = {v}")

    if args.dry_run:
        print("  (dry-run: nothing written)")
        return

    for key, val, day in metric_writes:
        store["metrics"].setdefault(key, []).append({"date": day, "value": val})
    for key in store["metrics"]:
        store["metrics"][key].sort(key=lambda e: e["date"])
    health_metrics.save(store)

    # cross_dedup=True: skip an Apple workout if the same date+type session already
    # exists from another source (HEVY, Telegram, …) so we never double-count.
    new_w = sum(1 for w in workouts if activity_log.append_entry(w, cross_dedup=True))
    print(f"  ✓ logged {len(metric_writes)} metric readings, {new_w} new workouts "
          f"(activity log total {activity_log.count()}).")


if __name__ == "__main__":
    main()
