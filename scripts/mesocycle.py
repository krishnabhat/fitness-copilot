#!/usr/bin/env python3
"""
mesocycle.py — track the athlete's periodization phase so the planner programs
progressively over weeks (build → deload → repeat), like a real coach.

Date-based: the current week/phase is derived from a start date, so it stays
correct with no manual updating. Default cycle = 4 weeks (3 build + 1 deload),
which suits an intermediate with moderate recovery (sleep ~6–7 h, higher stress).

State: ~/.claude/skills/fitness-copilot/profile/.mesocycle.json
  { "start_date": "YYYY-MM-DD", "cycle_weeks": 4, "build_weeks": 3,
    "cycle_number_at_start": 1 }

Usage:
  python3 mesocycle.py --status            # human-readable phase + programming guidance
  python3 mesocycle.py --start [DATE]      # (re)start a mesocycle block today or on DATE
  python3 mesocycle.py --init-if-missing   # create state starting today if none exists
"""

import json
import os
import sys
from datetime import date, datetime

STATE_FILE = os.path.expanduser(
    "~/.claude/skills/fitness-copilot/profile/.mesocycle.json"
)
DEFAULTS = {"cycle_weeks": 4, "build_weeks": 3, "cycle_number_at_start": 1}


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2)
    os.chmod(STATE_FILE, 0o600)


def start(start_date=None):
    s = load_state()
    s.setdefault("cycle_weeks", DEFAULTS["cycle_weeks"])
    s.setdefault("build_weeks", DEFAULTS["build_weeks"])
    s["start_date"] = start_date or date.today().isoformat()
    s["cycle_number_at_start"] = s.get("cycle_number_at_start", 1)
    save_state(s)
    print(f"Mesocycle (re)started {s['start_date']} "
          f"({s['build_weeks']} build + {s['cycle_weeks']-s['build_weeks']} deload).")


def status():
    s = load_state()
    if "start_date" not in s:
        print("No mesocycle on record. Run --start to begin one.")
        return 1
    start_d = datetime.fromisoformat(s["start_date"]).date()
    cycle_weeks = int(s.get("cycle_weeks", 4))
    build_weeks = int(s.get("build_weeks", 3))
    base_cycle = int(s.get("cycle_number_at_start", 1))

    days = (date.today() - start_d).days
    week_index = max(0, days // 7)              # 0-based weeks since start
    cycle_number = base_cycle + (week_index // cycle_weeks)
    pos = (week_index % cycle_weeks) + 1         # 1-based week within the cycle
    is_deload = pos > build_weeks

    print(f"Mesocycle #{cycle_number} · week {pos} of {cycle_weeks} "
          f"(started {s['start_date']}, day {days}).")
    if is_deload:
        print("Phase: DELOAD — recovery week.")
        print("Program: cut working volume ~40–50% (fewer sets) and/or drop intensity "
              "~10%; keep the movements but stay well shy of failure (RIR 3–4). "
              "Goal is to dissipate fatigue so next cycle starts fresh and stronger.")
    else:
        print(f"Phase: ACCUMULATION — build week {pos} of {build_weeks}.")
        if pos == 1:
            print("Program: re-establish/just beat last cycle's starting loads; moderate "
                  "volume (~RIR 2–3). Add a little vs the comparable session last cycle.")
        elif pos < build_weeks:
            print("Program: progressive overload — add reps toward the top of each range, "
                  "or +1 set on a lagging muscle, or small load bumps. Push toward RIR 1–2.")
        else:
            print("Program: peak week — highest volume/intensity of the cycle, RIR 0–1 on "
                  "isolation, RIR 1–2 on big compounds. Then next week deloads.")
    print("Per-lift rule: double progression — when all sets hit the top of the rep range "
          "at the target RIR, add load next time. Always compare to the last comparable "
          "HEVY session and beat it where readiness allows. Each new cycle should start "
          "above where the previous build began.")
    print("Override: if readiness is poor (bad sleep, high soreness/stress, aches), "
          "treat the day as a partial deload regardless of phase.")
    return 0


def main():
    if "--start" in sys.argv:
        i = sys.argv.index("--start")
        d = sys.argv[i + 1] if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-") else None
        start(d)
        return 0
    if "--init-if-missing" in sys.argv:
        if "start_date" not in load_state():
            start()
        else:
            print("Mesocycle already initialized.")
        return 0
    return status()


if __name__ == "__main__":
    sys.exit(main())
