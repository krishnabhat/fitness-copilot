#!/usr/bin/env python3
"""
hevy_sync.py — pull recent workout data from the HEVY public API for the
fitness-copilot skill, and print a compact, token-efficient summary.

Auth: requires a Hevy Pro API key. Provide it via (in priority order):
  1. env var  HEVY_API_KEY
  2. file     ~/.claude/skills/fitness-copilot/profile/.hevy_key  (single line)

Examples:
  python3 hevy_sync.py --recent 10            # last 10 workouts (summary)
  python3 hevy_sync.py --days 14              # workouts in the last 14 days
  python3 hevy_sync.py --routines             # saved routines
  python3 hevy_sync.py --exercise "Bench Press"   # history/progression for one lift
  python3 hevy_sync.py --recent 5 --json      # raw JSON for those workouts

The API key is never printed. Only stdlib is used (no pip install needed).
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_BASE = "https://api.hevyapp.com/v1"
KEY_FILE = os.path.expanduser(
    "~/.claude/skills/fitness-copilot/profile/.hevy_key"
)
TEMPLATE_CACHE = os.path.expanduser(
    "~/.claude/skills/fitness-copilot/profile/.hevy_templates_cache.json"
)


def get_api_key():
    key = os.environ.get("HEVY_API_KEY", "").strip()
    if key:
        return key
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE) as f:
            key = f.read().strip()
        if key:
            return key
    sys.exit(
        "ERROR: No HEVY API key found.\n"
        "  Set HEVY_API_KEY env var, or write the key to:\n"
        f"  {KEY_FILE}\n"
        "  Get a key at hevy.com -> Settings -> Developer/API (requires Hevy Pro)."
    )


def api_get(path, params=None):
    url = f"{API_BASE}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("api-key", get_api_key())
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if e.code in (401, 403):
            sys.exit(
                f"ERROR: HEVY API auth failed ({e.code}). Check the API key and "
                "that the account has Hevy Pro. (key not shown)"
            )
        sys.exit(f"ERROR: HEVY API {e.code} on {path}: {body[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"ERROR: could not reach HEVY API ({e.reason}). Offline?")


def paginate(path, page_size=10, max_items=None, items_key=None):
    """Yield items from a paginated HEVY endpoint."""
    page = 1
    fetched = 0
    while True:
        data = api_get(path, {"page": page, "pageSize": page_size})
        # endpoints wrap the list under different keys; auto-detect.
        if items_key and items_key in data:
            items = data[items_key]
        else:
            items = next(
                (v for v in data.values() if isinstance(v, list)), []
            )
        for it in items:
            yield it
            fetched += 1
            if max_items and fetched >= max_items:
                return
        page_count = data.get("page_count", 1)
        if page >= page_count or not items:
            return
        page += 1


# ---------- exercise template -> muscle group mapping (cached) ----------

def load_template_map():
    if os.path.exists(TEMPLATE_CACHE):
        try:
            with open(TEMPLATE_CACHE) as f:
                return json.load(f)
        except Exception:
            pass
    mapping = {}
    try:
        for t in paginate(
            "exercise_templates", page_size=100, items_key="exercise_templates"
        ):
            mapping[t.get("id")] = {
                "title": t.get("title"),
                "primary": t.get("primary_muscle_group"),
                "secondary": t.get("secondary_muscle_groups") or [],
                "type": t.get("type"),
            }
        with open(TEMPLATE_CACHE, "w") as f:
            json.dump(mapping, f)
    except SystemExit:
        # don't let muscle-mapping kill the whole run
        return {}
    return mapping


# ---------- formatting helpers ----------

def kg_to_lb(kg):
    return round(kg * 2.20462, 1)


# display units: "kg" or "lb". Set from --units / profile in main().
UNITS = "kg"
PROFILE_FILE = os.path.expanduser(
    "~/.claude/skills/fitness-copilot/profile/profile.md"
)


def detect_units():
    """Read 'Preferred units:' from the profile; default kg. lb if it mentions lb."""
    try:
        with open(PROFILE_FILE) as f:
            for line in f:
                if "preferred units" in line.lower():
                    return "lb" if "lb" in line.lower() else "kg"
    except OSError:
        pass
    return "kg"


def _trim(n):
    """4.0 -> '4', 4.5 -> '4.5'."""
    return f"{n:.1f}".rstrip("0").rstrip(".")


def fmt_weight(kg):
    """Format a kg weight in the active display unit."""
    if kg is None:
        return None
    if UNITS == "lb":
        lb = round(kg * 2.20462 * 2) / 2  # nearest 0.5 lb
        return f"{_trim(lb)}lb"
    return f"{_trim(round(kg, 1))}kg"


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def fmt_set(s):
    parts = []
    w = s.get("weight_kg")
    reps = s.get("reps")
    dur = s.get("duration_seconds")
    dist = s.get("distance_meters")
    rpe = s.get("rpe")
    stype = s.get("type")
    if w is not None and reps is not None:
        parts.append(f"BW×{reps}" if w == 0 else f"{fmt_weight(w)}×{reps}")
    elif reps is not None:
        parts.append(f"BW×{reps}" if w in (None, 0) else f"{fmt_weight(w)}×{reps}")
    if dist is not None:
        parts.append(f"{dist}m")
    if dur is not None:
        parts.append(f"{dur}s")
    label = " ".join(parts) if parts else "(empty)"
    if rpe is not None:
        label += f" @RPE{rpe}"
    if stype and stype not in ("normal", "working"):
        label += f" [{stype}]"
    return label


def summarize_workout(w, tmap, with_volume=True):
    lines = []
    start = parse_dt(w.get("start_time"))
    end = parse_dt(w.get("end_time"))
    date = start.astimezone().strftime("%a %Y-%m-%d %H:%M") if start else "?"
    dur = ""
    if start and end:
        mins = int((end - start).total_seconds() // 60)
        dur = f"  ({mins} min)"
    lines.append(f"■ {date}{dur} — {w.get('title','(untitled)')}")
    if w.get("description"):
        lines.append(f"    note: {w['description']}")
    muscle_sets = {}
    for ex in w.get("exercises", []):
        sets = ex.get("sets", [])
        working = [s for s in sets if s.get("type") not in ("warmup",)]
        set_strs = [fmt_set(s) for s in working]
        title = ex.get("title", "?")
        lines.append(f"    • {title}: " + " | ".join(set_strs))
        if with_volume:
            tid = ex.get("exercise_template_id")
            info = tmap.get(tid) if tid else None
            primary = (info or {}).get("primary")
            if primary:
                muscle_sets[primary] = muscle_sets.get(primary, 0) + len(working)
    if with_volume and muscle_sets:
        vol = ", ".join(
            f"{m} {n}" for m, n in sorted(
                muscle_sets.items(), key=lambda x: -x[1]
            )
        )
        lines.append(f"    sets by muscle: {vol}")
    return "\n".join(lines), muscle_sets


def cmd_workouts(args):
    tmap = load_template_map() if not args.json else {}
    collected = []
    cutoff = None
    if args.days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    limit = args.recent if not args.days else 50  # safety cap when by-days
    for w in paginate("workouts", page_size=10, max_items=limit,
                      items_key="workouts"):
        if cutoff:
            st = parse_dt(w.get("start_time"))
            if st and st < cutoff:
                break
        collected.append(w)

    if args.json:
        print(json.dumps(collected, indent=2))
        return

    if not collected:
        print("No workouts found.")
        return

    print(f"=== HEVY: {len(collected)} workout(s) "
          f"{'in last %d days' % args.days if args.days else '(most recent)'} ===\n")
    total_muscle = {}
    blocks = []
    for w in collected:
        block, msets = summarize_workout(w, tmap)
        blocks.append(block)
        for m, n in msets.items():
            total_muscle[m] = total_muscle.get(m, 0) + n
    print("\n\n".join(blocks))
    if total_muscle:
        print("\n--- Total working sets by muscle (this window) ---")
        for m, n in sorted(total_muscle.items(), key=lambda x: -x[1]):
            print(f"  {m}: {n}")
    # weekly cadence hint
    dates = [parse_dt(w.get("start_time")) for w in collected]
    dates = [d for d in dates if d]
    if len(dates) >= 2:
        span_days = (max(dates) - min(dates)).days or 1
        per_week = round(len(dates) / (span_days / 7), 1)
        print(f"\nFrequency: ~{per_week} workouts/week over last {span_days} days.")


def cmd_routines(args):
    routines = list(paginate("routines", page_size=10, items_key="routines"))
    if args.json:
        print(json.dumps(routines, indent=2))
        return
    if not routines:
        print("No routines found.")
        return
    print(f"=== HEVY routines ({len(routines)}) ===\n")
    for r in routines:
        print(f"■ {r.get('title','(untitled)')}")
        for ex in r.get("exercises", []):
            sets = ex.get("sets", [])
            print(f"    • {ex.get('title','?')}: {len(sets)} sets")
        print()


def cmd_exercise(args):
    """Show history/progression for one exercise across recent workouts."""
    tmap = load_template_map()
    target = args.exercise.lower().strip()
    hits = []
    for w in paginate("workouts", page_size=10, max_items=100,
                      items_key="workouts"):
        st = parse_dt(w.get("start_time"))
        for ex in w.get("exercises", []):
            if target in (ex.get("title", "").lower()):
                working = [s for s in ex.get("sets", [])
                           if s.get("type") not in ("warmup",)]
                hits.append((st, ex.get("title"), working))
    if not hits:
        print(f"No history found for exercise matching '{args.exercise}'.")
        return
    print(f"=== Progression: '{args.exercise}' (most recent first) ===\n")
    for st, title, working in hits[:args.recent or 12]:
        date = st.astimezone().strftime("%Y-%m-%d") if st else "?"
        best = None
        for s in working:
            w_, r_ = s.get("weight_kg"), s.get("reps")
            if w_ is not None and r_ is not None:
                est1rm = round(w_ * (1 + r_ / 30.0), 1)  # Epley
                if not best or est1rm > best[0]:
                    best = (est1rm, w_, r_)
        sets_str = " | ".join(fmt_set(s) for s in working)
        line = f"{date}  {title}: {sets_str}"
        if best:
            line += f"   (est 1RM ~{fmt_weight(best[0])})"
        print(line)


def _best_set(working):
    best = None
    for s in working:
        w_, r_ = s.get("weight_kg"), s.get("reps")
        if w_ is not None and r_ is not None:
            est = w_ * (1 + r_ / 30.0)  # Epley est 1RM
            if not best or est > best[0]:
                best = (est, w_, r_)
    return best


def cmd_progress(args):
    """Per-lift trajectory across recent workouts — supports progressive overload."""
    scan = args.recent if args.recent and args.recent > 12 else 40
    by_ex = {}
    for w in paginate("workouts", page_size=10, max_items=scan, items_key="workouts"):
        st = parse_dt(w.get("start_time"))
        for ex in w.get("exercises", []):
            title = ex.get("title", "?")
            working = [s for s in ex.get("sets", []) if s.get("type") not in ("warmup",)]
            if working:
                by_ex.setdefault(title, []).append((st, working))
    # only lifts trained at least twice, most-frequent first
    repeated = {k: v for k, v in by_ex.items() if len(v) >= 2}
    if not repeated:
        print("Not enough repeated lifts yet to show progression trends.")
        return
    print("=== Lift progression (oldest → newest; for progressive overload) ===\n")
    for title in sorted(repeated, key=lambda k: -len(repeated[k])):
        sessions = sorted(repeated[title], key=lambda x: x[0] or datetime.min.replace(tzinfo=timezone.utc))
        points = []
        first_e = last_e = None
        for st, working in sessions:
            d = st.astimezone().strftime("%m/%d") if st else "?"
            b = _best_set(working)
            if b:
                points.append(f"{d}:{fmt_weight(b[1])}×{b[2]}")
                if first_e is None:
                    first_e = b[0]
                last_e = b[0]
        if not points:
            continue
        trend = ""
        if first_e and last_e:
            pct = (last_e - first_e) / first_e * 100
            arrow = "▲" if pct > 1 else ("▼" if pct < -1 else "▬")
            trend = f"   {arrow} est1RM {fmt_weight(last_e)} ({pct:+.0f}%)"
        print(f"• {title} ({len(sessions)}x): " + "  ".join(points) + trend)


def main():
    p = argparse.ArgumentParser(description="Pull HEVY workout data.")
    p.add_argument("--recent", type=int, default=10,
                   help="number of most recent workouts (default 10)")
    p.add_argument("--days", type=int,
                   help="workouts within the last N days (overrides --recent count)")
    p.add_argument("--routines", action="store_true",
                   help="list saved routines instead of workouts")
    p.add_argument("--exercise", type=str,
                   help="show history/progression for a single exercise name")
    p.add_argument("--progress", action="store_true",
                   help="per-lift progression trends across recent workouts")
    p.add_argument("--json", action="store_true",
                   help="print raw JSON instead of a summary")
    p.add_argument("--units", choices=["kg", "lb", "auto"], default="auto",
                   help="weight display units (default: auto from profile, else kg)")
    args = p.parse_args()

    global UNITS
    UNITS = detect_units() if args.units == "auto" else args.units

    if args.routines:
        cmd_routines(args)
    elif args.exercise:
        cmd_exercise(args)
    elif args.progress:
        cmd_progress(args)
    else:
        cmd_workouts(args)


if __name__ == "__main__":
    main()
