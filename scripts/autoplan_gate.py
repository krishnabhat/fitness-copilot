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
from datetime import date

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


def remind():
    """On a skipped (gated) night, re-send the pending workout HTML(s) to Telegram as
    a reminder. Guarded to fire at most once per day so the failsafe runs don't spam."""
    state = load_state()
    if "baseline_count" not in state:
        return
    today = date.today().isoformat()
    if state.get("last_remind") == today:
        print("already reminded today; skipping.")
        return
    pending = int(state.get("pending", 1))
    baseline = int(state.get("baseline_count", 0))
    remaining = max(0, pending - (workout_count() - baseline))
    if remaining <= 0:
        print("no pending workouts to remind about.")
        return
    if not os.path.exists(os.path.expanduser(
            "~/.claude/skills/fitness-copilot/profile/.telegram")):
        print("telegram not configured; skipping reminder.")
        return
    import glob
    import telegram_ingest
    import notify_telegram
    files = sorted(glob.glob(os.path.join(telegram_ingest.WORKOUTS_DIR, "*.html")),
                   key=os.path.getmtime, reverse=True)[:remaining]
    if not files:
        print("no workout HTML found to send.")
        return
    creds = notify_telegram.load_creds()
    notify_telegram.send_message(creds, f"⏳ Reminder: you still have {remaining} planned "
                                 f"workout{'s' if remaining > 1 else ''} to do. "
                                 f"Here {'they are' if remaining > 1 else 'it is'}:")
    for f in reversed(files):   # oldest pending first
        notify_telegram.send_document(creds, f, f"💪 {telegram_ingest._title_from_file(f)}")
    state["last_remind"] = today
    save_state(state)
    print(f"reminded with {len(files)} pending workout(s).")


def main():
    if "--remind" in sys.argv:
        remind()
        return 0
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
