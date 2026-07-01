#!/usr/bin/env python3
"""
autoplan_gate.py — decide whether the nightly auto-planner should plan a new
workout. Goal: never let unstarted routines pile up. Only plan once the athlete
has logged enough new activities to have cleared everything we've queued.

Watermark logic (robust to log dedupe/pruning — a mutable total count is not):
  state = { "last_plan_at": ISO8601-UTC, "pending": K }
  - At plan time we stamp last_plan_at = now and pending = how many routines are
    queued and unstarted.
  - The gate proceeds only when the athlete has logged >= pending activities whose
    logged_at is AFTER last_plan_at. Counting forward from a timestamp can't go
    negative when old rows are de-duped away (the old count-baseline could).

Usage:
  python3 autoplan_gate.py                 # check: exit 0 = plan now, 10 = skip
  python3 autoplan_gate.py --record        # mark: just planned 1 routine
  python3 autoplan_gate.py --record --pending N   # mark: N routines now queued
  python3 autoplan_gate.py --remind        # re-send pending workout(s) (once/day)

State: ~/.claude/skills/fitness-copilot/profile/.autoplan_state.json
Exit codes: 0 proceed · 10 skip · 2 error (wrapper treats non-zero as "don't plan").
"""

import json
import os
import sys
from datetime import date, datetime, timezone

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


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def done_since(state):
    """How many activities have been logged since the last plan.
    Returns None if there's no plan on record yet (first run)."""
    if state.get("last_plan_at"):
        return activity_log.count_since(state["last_plan_at"])
    # Legacy fallback for pre-watermark state; migrates to last_plan_at on next --record.
    if "baseline_count" in state:
        return max(0, activity_log.count() - int(state["baseline_count"]))
    return None


def record(pending):
    state = load_state()
    state["last_plan_at"] = _now_iso()
    state["pending"] = max(1, int(pending))
    state.pop("baseline_count", None)   # retire the count-based baseline
    save_state(state)
    print(f"recorded last_plan_at={state['last_plan_at']} pending={state['pending']}")


def check():
    state = load_state()
    d = done_since(state)
    if d is None:
        print("No prior plan on record → proceeding (first run).")
        return 0
    pending = int(state.get("pending", 1))
    if d >= pending:
        print(f"{d} activity(ies) logged since last plan (needed {pending}) → "
              "queue cleared, planning next.")
        return 0
    print(f"Only {d} of {pending} queued workout(s) completed since last plan "
          "→ not planning, leaving queue in place.")
    return 10


def remind():
    """On a skipped (gated) night, re-send the pending workout HTML(s) to Telegram as
    a reminder. Guarded to fire at most once per day so the failsafe runs don't spam."""
    state = load_state()
    d = done_since(state)
    if d is None:
        return
    today = date.today().isoformat()
    if state.get("last_remind") == today:
        print("already reminded today; skipping.")
        return
    pending = int(state.get("pending", 1))
    remaining = max(0, pending - d)
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
