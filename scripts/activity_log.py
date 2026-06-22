#!/usr/bin/env python3
"""
activity_log.py — the canonical LOCAL log of every activity the athlete performs,
regardless of source (HEVY, Telegram, manual entry, Apple Health in future).

Stored as append-only JSONL at profile/activity_log.jsonl. Each entry:
  { "logged_at": ISO, "date": "YYYY-MM-DD", "source": "hevy|telegram|manual|apple_health",
    "source_id": "<stable id for dedup, optional>", "type": "strength|run|bike|walk|
    yoga|hiit|swim|cardio|other", "title": str, "duration_min": num?, "distance_km": num?,
    "detail": str }

Other scripts import this module (append_entry, count, count_since, sync_hevy).

CLI:
  python3 activity_log.py --sync-hevy           # mirror new HEVY workouts into the log
  python3 activity_log.py --recent 15           # show recent activities (all sources)
  python3 activity_log.py --summary --days 30   # counts by type/source
  python3 activity_log.py --add --type run --title "Hill run" --duration 30 --distance 5
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hevy_sync  # noqa: E402

LOG = os.path.expanduser("~/.claude/skills/fitness-copilot/profile/activity_log.jsonl")


def read_all():
    out = []
    if os.path.exists(LOG):
        with open(LOG) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return out


def _existing_source_ids():
    return {(e.get("source"), e.get("source_id"))
            for e in read_all() if e.get("source_id")}


# Which source to trust when the SAME real session shows up from several sources.
# Higher = preferred (more detail / more authoritative for that session).
SOURCE_PRIORITY = {"hevy": 4, "apple_health": 3, "oura": 3, "telegram": 2, "manual": 1}


def _detail_score(e):
    return (1 if e.get("distance_km") else 0) + (1 if e.get("duration_min") else 0)


def has_session(date, atype, exclude_source=None, entries=None):
    """True if an activity with the same date+type exists (optionally from another source)."""
    for e in (entries if entries is not None else read_all()):
        if e.get("date") == date and e.get("type") == atype:
            if exclude_source is None or e.get("source") != exclude_source:
                return True
    return False


def _write_all(entries):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    os.chmod(LOG, 0o600)


def append_entry(entry, dedup=True, cross_dedup=False):
    """Append one activity. Returns True if written, False if a dup was skipped.
    cross_dedup=True also skips if the same date+type already exists from ANOTHER source
    (i.e. the same real session already came in via HEVY/Telegram/etc.)."""
    if dedup and entry.get("source_id"):
        if (entry.get("source"), entry["source_id"]) in _existing_source_ids():
            return False
    if cross_dedup and entry.get("date") and entry.get("type"):
        if has_session(entry["date"], entry["type"], exclude_source=entry.get("source")):
            return False
    entry.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
    entry.setdefault("date", entry["logged_at"][:10])
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    if not os.path.exists(LOG + ".lock"):
        os.chmod(LOG, 0o600)
    return True


def count():
    return len(read_all())


def count_since(iso):
    return sum(1 for e in read_all() if e.get("logged_at", "") > iso)


def _infer_hevy_type(workout):
    has_cardio = has_lift = False
    for ex in workout.get("exercises", []):
        for s in ex.get("sets", []):
            if s.get("weight_kg"):
                has_lift = True
            if s.get("distance_meters") or s.get("duration_seconds"):
                has_cardio = True
    if has_lift:
        return "strength"
    return "cardio" if has_cardio else "strength"


def sync_hevy(max_items=60):
    """Mirror recent HEVY workouts into the local log (dedup by workout id)."""
    new = 0
    for w in hevy_sync.paginate("workouts", page_size=10, max_items=max_items,
                                items_key="workouts"):
        st = hevy_sync.parse_dt(w.get("start_time"))
        et = hevy_sync.parse_dt(w.get("end_time"))
        dur = int((et - st).total_seconds() // 60) if st and et else None
        names = ", ".join(ex.get("title", "?") for ex in w.get("exercises", []))
        entry = {
            "source": "hevy",
            "source_id": w.get("id"),
            "date": st.astimezone().strftime("%Y-%m-%d") if st else None,
            "type": _infer_hevy_type(w),
            "title": w.get("title") or "HEVY workout",
            "duration_min": dur,
            "detail": names[:300],
        }
        if append_entry(entry):
            new += 1
    print(f"HEVY sync: {new} new activity(ies) mirrored into the local log "
          f"(total {count()}).")
    return new


def dedupe(dry_run=False):
    """Collapse cross-source duplicates: entries sharing date+type from DIFFERENT
    sources are treated as the same real session — keep the best one (most detail,
    then source priority). Multiple same-source entries (e.g. two texted runs) are kept."""
    from collections import defaultdict
    entries = read_all()
    groups = defaultdict(list)
    for e in entries:
        groups[(e.get("date"), e.get("type"))].append(e)
    kept, removed = [], []
    for grp in groups.values():
        sources = {e.get("source") for e in grp}
        if len(grp) == 1 or len(sources) == 1:
            kept.extend(grp)          # single, or intentional same-source multiples
        else:
            best = max(grp, key=lambda e: (_detail_score(e),
                                           SOURCE_PRIORITY.get(e.get("source"), 0)))
            kept.append(best)
            removed.extend(e for e in grp if e is not best)
    kept.sort(key=lambda e: (e.get("date") or "", e.get("logged_at") or ""))
    print(f"Cross-source dedup: {len(entries)} entries → {len(kept)} kept, "
          f"{len(removed)} duplicate(s) removed.")
    for e in removed[:12]:
        print(f"   - {e.get('date')} [{e.get('source')}] {e.get('type')} — {e.get('title','')}")
    if not dry_run and removed:
        _write_all(kept)
        print("  ✓ log cleaned.")
    elif dry_run:
        print("  (dry-run: nothing changed)")
    return removed


def recent(n):
    entries = read_all()[-n:]
    if not entries:
        print("No activities logged yet.")
        return
    print(f"=== Local activity log (last {len(entries)}, all sources) ===")
    for e in entries:
        bits = [e.get("date", "?"), f"[{e.get('source','?')}]", e.get("type", "?"),
                "—", e.get("title", "")]
        extra = []
        if e.get("distance_km"):
            extra.append(f"{e['distance_km']} km")
        if e.get("duration_min"):
            extra.append(f"{e['duration_min']} min")
        line = " ".join(bits)
        if extra:
            line += " (" + ", ".join(extra) + ")"
        print("• " + line)


def summary(days):
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = [e for e in read_all() if (e.get("date") or "") >= cutoff]
    print(f"=== Activity summary (last {days} days, {len(rows)} activities) ===")
    by_type, by_src = {}, {}
    for e in rows:
        by_type[e.get("type", "?")] = by_type.get(e.get("type", "?"), 0) + 1
        by_src[e.get("source", "?")] = by_src.get(e.get("source", "?"), 0) + 1
    print("by type:  " + ", ".join(f"{k} {v}" for k, v in sorted(by_type.items(), key=lambda x: -x[1])))
    print("by source:" + ", ".join(f" {k} {v}" for k, v in sorted(by_src.items(), key=lambda x: -x[1])))


def history(days):
    """Unified workout history across ALL sources (strength, runs, HIIT, etc.)."""
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = [e for e in read_all() if (e.get("date") or "") >= cutoff]
    rows.sort(key=lambda e: ((e.get("date") or ""), (e.get("logged_at") or "")), reverse=True)
    if not rows:
        print(f"No activities in the last {days} days.")
        return
    print(f"=== Workout history — last {days} days ({len(rows)} activities, all sources) ===")
    for e in rows:
        extra = []
        if e.get("distance_km"):
            extra.append(f"{e['distance_km']} km")
        if e.get("duration_min"):
            extra.append(f"{int(e['duration_min'])} min")
        tail = ("  (" + ", ".join(extra) + ")") if extra else ""
        print(f"  {e.get('date','?')}  [{e.get('source','?')[:8]:8}] {e.get('type','?'):9} "
              f"{e.get('title','')}{tail}")
    by_type = {}
    for e in rows:
        by_type[e.get("type", "?")] = by_type.get(e.get("type", "?"), 0) + 1
    print("  by type: " + ", ".join(f"{k} {v}" for k, v in sorted(by_type.items(), key=lambda x: -x[1])))


def main():
    p = argparse.ArgumentParser(description="Canonical local activity log.")
    p.add_argument("--sync-hevy", action="store_true")
    p.add_argument("--history", action="store_true", help="unified history (all sources) over --days")
    p.add_argument("--dedupe", action="store_true", help="remove cross-source duplicates")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--recent", type=int, metavar="N")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--add", action="store_true", help="add a manual activity")
    p.add_argument("--type", default="other")
    p.add_argument("--title", default="")
    p.add_argument("--duration", type=float)
    p.add_argument("--distance", type=float)
    p.add_argument("--detail", default="")
    p.add_argument("--date")
    p.add_argument("--source", default="manual")
    args = p.parse_args()

    did = False
    if args.sync_hevy:
        sync_hevy(); did = True
    if args.dedupe:
        dedupe(dry_run=args.dry_run); did = True
    if args.history:
        history(args.days); did = True
    if args.add:
        e = {"source": args.source, "type": args.type, "title": args.title or args.type,
             "detail": args.detail}
        if args.duration:
            e["duration_min"] = args.duration
        if args.distance:
            e["distance_km"] = args.distance
        if args.date:
            e["date"] = args.date
        append_entry(e, dedup=False)
        print(f"logged: {e['type']} — {e['title']}")
        did = True
    if args.recent:
        recent(args.recent); did = True
    if args.summary:
        summary(args.days); did = True
    if not did:
        recent(15)


if __name__ == "__main__":
    main()
