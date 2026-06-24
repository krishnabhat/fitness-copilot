#!/usr/bin/env python3
"""
hevy_push.py — create a HEVY *routine* from a workout spec, so the athlete opens
it in HEVY and just logs what they did (check off sets) instead of building the
workout exercise-by-exercise mid-session.

It consumes the SAME JSON spec that scripts/build_workout.py renders to HTML, so
one spec drives both the HTML session page and the HEVY routine.

Usage:
  python3 hevy_push.py spec.json                # create the routine in HEVY
  cat spec.json | python3 hevy_push.py -        # spec from stdin
  python3 hevy_push.py spec.json --dry-run      # resolve + preview, do NOT create
  python3 hevy_push.py spec.json --title "Mon: Lower"   # override routine title
  python3 hevy_push.py spec.json --include-warmup       # also add warm-up items

Requires a Hevy Pro API key (same as hevy_sync.py): env HEVY_API_KEY or the file
~/.claude/skills/fitness-copilot/profile/.hevy_key. The key is never printed.

Exercise names in the spec are matched to HEVY exercise templates. Exact and
case-insensitive matches are used first, then a close fuzzy match. Anything that
can't be confidently matched is reported and skipped (the routine is still created
with the matched exercises) — rename it in the spec to a standard HEVY exercise
name and re-run, or pre-create that custom exercise in the HEVY app.
"""

import argparse
import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.request

# reuse helpers from the sibling sync script (same directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hevy_sync  # noqa: E402


ACTIVE_FILE = os.path.expanduser(
    "~/.claude/skills/fitness-copilot/profile/.hevy_active_routine.json")


def load_active():
    if os.path.exists(ACTIVE_FILE):
        try:
            return json.load(open(ACTIVE_FILE))
        except Exception:
            return {}
    return {}


def save_active(d):
    with open(ACTIVE_FILE, "w") as f:
        json.dump(d, f)
    os.chmod(ACTIVE_FILE, 0o600)


def extract_routine_id(resp):
    if isinstance(resp, dict):
        r = resp.get("routine", resp)
        if isinstance(r, list) and r:
            r = r[0]
        if isinstance(r, dict):
            return r.get("id")
    return None


def api_post(path, payload, method="POST"):
    url = f"{hevy_sync.API_BASE}/{path.lstrip('/')}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("api-key", hevy_sync.get_api_key())
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        if e.code in (401, 403):
            sys.exit(f"ERROR: HEVY auth failed ({e.code}). Check key / Hevy Pro.")
        if e.code == 404:
            return None   # routine no longer exists → caller falls back to create
        sys.exit(f"ERROR: HEVY API {e.code} on {method} {path}: {detail[:400]}")
    except urllib.error.URLError as e:
        sys.exit(f"ERROR: could not reach HEVY API ({e.reason}).")


# ---------- parsing the human prescription into HEVY set fields ----------

def parse_int_reps(reps):
    """'5' -> 5 ; '10/leg' -> 10 ; '8-12' -> 12 (top of range) ; 'AMRAP' -> None."""
    if reps is None:
        return None
    s = str(reps)
    rng = re.findall(r"\d+", s)
    if not rng:
        return None
    # for a range like 8-12, target the top; otherwise the first number
    return int(rng[-1]) if "-" in s and len(rng) >= 2 else int(rng[0])


def parse_weight_kg(load):
    """'102.5kg / RPE 8' -> 102.5 ; '2x20kg DB' -> 20.0 ; 'RPE 8' -> None."""
    if not load:
        return None
    m = re.search(r"([\d.]+)\s*kg", str(load), re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"([\d.]+)\s*lb", str(load), re.IGNORECASE)
    if m:
        return round(float(m.group(1)) / 2.20462, 1)
    return None


def parse_rpe(load):
    if not load:
        return None
    m = re.search(r"rpe\s*([\d.]+)", str(load), re.IGNORECASE)
    return float(m.group(1)) if m else None


def superset_id_for(label, registry):
    """Group supersets by their alpha prefix: 'A1','A2' -> same id; 'B1' -> new id."""
    if not label:
        return None
    key = re.match(r"[A-Za-z]+", str(label))
    key = key.group(0).upper() if key else str(label)
    if key not in registry:
        registry[key] = len(registry)
    return registry[key]


# ---------- template name resolution ----------

def build_name_index(tmap):
    idx = {}
    for tid, info in tmap.items():
        title = (info or {}).get("title")
        if title:
            idx[title.strip().lower()] = (tid, title)
    return idx


def resolve_template(name, idx):
    if not name:
        return None, None, "no name"
    key = name.strip().lower()
    if key in idx:
        tid, title = idx[key]
        return tid, title, "exact"
    # fuzzy
    close = difflib.get_close_matches(key, list(idx.keys()), n=1, cutoff=0.82)
    if close:
        tid, title = idx[close[0]]
        return tid, title, f"fuzzy~'{title}'"
    return None, None, "unmatched"


def make_sets(ex):
    n = ex.get("sets")
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 1
    n = max(1, n)
    reps = parse_int_reps(ex.get("reps"))
    weight = parse_weight_kg(ex.get("load"))
    # NB: HEVY's routine endpoint does NOT accept an `rpe` field on sets (that's a
    # logged-workout field). RPE targets are carried in the exercise notes instead.
    one = {
        "type": "normal",
        "weight_kg": weight,
        "reps": reps,
        "distance_meters": None,
        "duration_seconds": None,
        "custom_metric": None,
    }
    return [dict(one) for _ in range(n)]


def exercise_notes(ex):
    bits = []
    if ex.get("load"):
        bits.append(f"target {ex['load']}")
    if ex.get("tempo"):
        bits.append(f"tempo {ex['tempo']}")
    if ex.get("target"):
        bits.append(ex["target"])
    if ex.get("cues"):
        bits.append("cues: " + "; ".join(ex["cues"]))
    return " | ".join(bits)[:480]


def collect_exercises(spec, include_warmup):
    """Flatten the spec's blocks (and optionally warm-up) into ordered exercises."""
    out = []
    if include_warmup:
        for w in spec.get("warmup", []) or []:
            if isinstance(w, dict) and w.get("name"):
                out.append({"name": w["name"], "sets": 1,
                            "reps": None, "load": w.get("detail"),
                            "_warmup": True})
    for block in spec.get("blocks", []) or []:
        for ex in block.get("exercises", []) or []:
            out.append(ex)
    cond = spec.get("conditioning")
    if cond:
        for ex in cond.get("exercises", []) or []:
            out.append(ex)
    return out


def main():
    p = argparse.ArgumentParser(description="Create a HEVY routine from a workout spec.")
    p.add_argument("spec", help="path to JSON spec, or '-' for stdin")
    p.add_argument("--title", help="override routine title")
    p.add_argument("--dry-run", action="store_true",
                   help="resolve templates and preview the payload; do not create")
    p.add_argument("--include-warmup", action="store_true",
                   help="also add warm-up items as exercises in the routine")
    p.add_argument("--new-routine", action="store_true",
                   help="force-create a new routine instead of updating the rolling one")
    args = p.parse_args()

    raw = sys.stdin.read() if args.spec == "-" else open(args.spec).read()
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: spec is not valid JSON: {e}")

    tmap = hevy_sync.load_template_map()
    if not tmap:
        sys.exit("ERROR: could not load HEVY exercise templates (need API key / Pro).")
    idx = build_name_index(tmap)

    title = args.title or spec.get("title") or "Coach session"
    if spec.get("date"):
        title = f"{title} ({spec['date']})"

    notes_parts = []
    if spec.get("focus"):
        notes_parts.append(spec["focus"])
    if spec.get("why"):
        notes_parts.append(spec["why"])
    routine_notes = " — ".join(notes_parts)[:480]

    ss_registry = {}
    routine_exercises = []
    matched, skipped = [], []
    for ex in collect_exercises(spec, args.include_warmup):
        name = ex.get("name")
        tid, title_match, how = resolve_template(name, idx)
        if not tid:
            skipped.append(name)
            continue
        matched.append((name, title_match, how))
        rex = {
            "exercise_template_id": tid,
            "superset_id": superset_id_for(ex.get("superset"), ss_registry),
            "rest_seconds": ex.get("rest_sec"),
            "notes": exercise_notes(ex),
            "sets": ([{"type": "warmup", "weight_kg": None, "reps": None,
                       "distance_meters": None, "duration_seconds": None,
                       "custom_metric": None}]
                     if ex.get("_warmup") else make_sets(ex)),
        }
        routine_exercises.append(rex)

    if not routine_exercises:
        sys.exit("ERROR: no exercises could be matched to HEVY templates; nothing to create.\n"
                 "  Unmatched: " + ", ".join(s or "?" for s in skipped))

    payload = {"routine": {
        "title": title,
        "notes": routine_notes,
        "exercises": routine_exercises,
    }}

    print(f"Routine: {title}")
    print(f"Matched {len(matched)} exercise(s):")
    for name, tmatch, how in matched:
        flag = "" if how == "exact" else f"   [{how}]"
        print(f"   ✓ {name}{flag}")
    if skipped:
        print(f"Skipped {len(skipped)} unmatched (rename to a standard HEVY exercise "
              f"or pre-create in the app, then re-run):")
        for s in skipped:
            print(f"   ✗ {s}")

    active = load_active()
    active_id = active.get("id")
    reuse = bool(active_id) and not args.new_routine

    if args.dry_run:
        action = f"UPDATE existing routine in place (id {active_id})" if reuse \
            else "CREATE a new routine"
        print(f"\n--dry-run: would {action}. payload preview (not sent) ---")
        print(json.dumps(payload, indent=2))
        return

    if reuse:
        # Keep the same routine while the current plan is pending: update it in place.
        resp = api_post(f"routines/{active_id}", payload, method="PUT")
        if resp is not None:
            save_active({"id": active_id, "title": title})
            print(f"\n✓ Updated your rolling routine in HEVY (id {active_id}). "
                  "Same routine, refreshed with this plan — no duplicate created.")
            print("  Open HEVY → Routines → it's the same one, now showing today's session.")
            return
        # 404: the routine was deleted in the app → fall through and create a fresh one.
        print("  (previous routine no longer in HEVY — creating a fresh one.)")

    resp = api_post("routines", payload)
    rid = extract_routine_id(resp)
    save_active({"id": rid, "title": title})
    print(f"\n✓ Created a new routine in HEVY{f' (id {rid})' if rid else ''}.")
    print("  Open HEVY → Routines → start it → just log the sets you actually did.")


if __name__ == "__main__":
    main()
