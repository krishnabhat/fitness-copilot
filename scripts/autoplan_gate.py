#!/usr/bin/env python3
"""
autoplan_gate.py — decide whether the nightly auto-planner should plan a new
workout. Goal: never let unstarted routines pile up. Only plan once the athlete
has logged enough new workouts in HEVY to have cleared everything we've queued.

Count-based logic (robust to multiple pending routines):
  state = { "baseline_count": N, "pending": K }
  - At plan time we record baseline_count = total HEVY workouts then, and
    pending = how many routines are now queued and unstarted.
  - The gate proceeds only when (current_count - baseline_count) >= pending,
    i.e. the athlete has logged at least `pending` new workouts since we planned.
  Otherwise it skips, leaving the queued routine(s) in place.

Usage:
  python3 autoplan_gate.py                 # check: exit 0 = plan now, 10 = skip
  python3 autoplan_gate.py --record        # mark: just planned 1 routine
  python3 autoplan_gate.py --record --pending N   # mark: N routines now queued

State: ~/.claude/skills/fitness-copilot/profile/.autoplan_state.json
Exit codes: 0 proceed · 10 skip · 2 error (wrapper treats non-zero as "don't plan").
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import activity_log  # noqa: E402  (canonical local log — union of all sources)

STATE_FILE = os.path.expanduser(
    "~/.claude/skills/fitness-copilot/profile/.autoplan_state.json"
)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)
    os.chmod(STATE_FILE, 0o600)


def workout_count():
    # Count from the canonical local log (union of HEVY + Telegram + manual + ...),
    # so activities logged via any source count as "trained". The nightly wrapper
    # runs `activity_log.py --sync-hevy` first to mirror HEVY into the log.
    return activity_log.count()


def record(pending):
    state = load_state()
    state["baseline_count"] = workout_count()
    state["pending"] = max(1, int(pending))
    save_state(state)
    print(f"recorded baseline_count={state.get('baseline_count')} pending={state['pending']}")


def check():
    state = load_state()
    if "baseline_count" not in state:
        print("No baseline on record → proceeding (first run).")
        return 0
    current = workout_count()
    baseline = int(state.get("baseline_count", 0))
    pending = int(state.get("pending", 1))
    done = current - baseline
    if done >= pending:
        print(f"{done} workout(s) logged since last plan (needed {pending}) → "
              f"queue cleared, planning next.")
        return 0
    print(f"Only {done} of {pending} queued workout(s) completed since last plan "
          f"(HEVY total {current}, baseline {baseline}) → not planning, leaving queue in place.")
    return 10


def main():
    if "--record" in sys.argv:
        pending = 1
        if "--pending" in sys.argv:
            i = sys.argv.index("--pending")
            if i + 1 < len(sys.argv):
                pending = sys.argv[i + 1]
        record(pending)
        return 0
    return check()


if __name__ == "__main__":
    sys.exit(main())
