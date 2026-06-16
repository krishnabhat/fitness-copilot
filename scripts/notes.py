#!/usr/bin/env python3
"""
notes.py — store pain / injury / status notes the athlete relays (e.g. via Telegram
"my lower back is tight today"). These are NOT activities — they're constraints the
planner reads to adjust the next session (avoid loading an injured area, go lighter
when run down). Kept separate from the activity log so they never count as workouts.

Store: profile/notes.jsonl  (append-only). Each: {logged_at, date, kind, red_flag, text}

CLI:
  python3 notes.py --add "left knee sore" --kind pain
  python3 notes.py --recent 7        # notes from the last 7 days
"""

import argparse
import json
import os
from datetime import datetime, timezone, date, timedelta

LOG = os.path.expanduser("~/.claude/skills/fitness-copilot/profile/notes.jsonl")


def append_note(text, kind="note", red_flag=False, when=None):
    entry = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "date": when or date.today().isoformat(),
        "kind": kind,                 # pain | status | note
        "red_flag": bool(red_flag),
        "text": text[:300],
    }
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    os.chmod(LOG, 0o600)
    return entry


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


def recent(days=7):
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = [e for e in read_all() if (e.get("date") or "") >= cutoff]
    if not rows:
        print(f"No notes in the last {days} days.")
        return rows
    print(f"=== Recent notes (last {days} days) — pain/status to program around ===")
    for e in rows:
        flag = " ⚠️RED-FLAG" if e.get("red_flag") else ""
        print(f"• {e.get('date')} [{e.get('kind')}]{flag}: {e.get('text')}")
    return rows


def main():
    p = argparse.ArgumentParser(description="Pain/injury/status notes for the planner.")
    p.add_argument("--add")
    p.add_argument("--kind", default="note", choices=["pain", "status", "note"])
    p.add_argument("--red-flag", action="store_true")
    p.add_argument("--date")
    p.add_argument("--recent", type=int, metavar="DAYS")
    args = p.parse_args()
    if args.add:
        e = append_note(args.add, kind=args.kind, red_flag=args.red_flag, when=args.date)
        print(f"noted [{e['kind']}]: {e['text']}")
    if args.recent:
        recent(args.recent)
    if not args.add and not args.recent:
        recent(7)


if __name__ == "__main__":
    main()
