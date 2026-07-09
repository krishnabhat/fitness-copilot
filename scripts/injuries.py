#!/usr/bin/env python3
"""
injuries.py — track active injuries/pain and their daily 1-10 severity, so the coach
can proactively check in each day and know when to return training to normal, instead
of waiting for the athlete to volunteer an update.

Store: profile/.injuries.json
  { "injuries": [ { "part": "lower back", "opened": "2026-07-06",
      "readings": [ {"date": "2026-07-06", "severity": 6} ],
      "resolved": false, "resolved_date": null, "last_checkin": "2026-07-06" } ] }

An injury auto-resolves once its two most recent daily readings are both <= RESOLVE_AT
(so training returns to normal only after it's genuinely calm, not on one good day).

CLI:
  python3 injuries.py --status              # active injuries + severity trend
  python3 injuries.py --open "lower back" [--severity 6]
  python3 injuries.py --log 3 [--part back] # record today's 1-10 reading
  python3 injuries.py --resolve "lower back"
"""

import argparse
import json
import os
from datetime import date

STORE = os.path.expanduser("~/.claude/skills/fitness-copilot/profile/.injuries.json")
RESOLVE_AT = 2   # <= this on the two latest readings → considered recovered


def load():
    if os.path.exists(STORE):
        try:
            return json.load(open(STORE))
        except Exception:
            pass
    return {"injuries": []}


def save(data):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(STORE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(STORE, 0o600)


def _match(part_a, part_b):
    a, b = (part_a or "").lower(), (part_b or "").lower()
    return a and b and (a in b or b in a)


def _find_active(data, part):
    for inj in data["injuries"]:
        if not inj.get("resolved") and _match(inj.get("part"), part):
            return inj
    return None


def active(data=None):
    data = data or load()
    return [i for i in data["injuries"] if not i.get("resolved")]


def _auto_resolve(inj):
    sev = [r["severity"] for r in inj.get("readings", []) if r.get("severity") is not None]
    if len(sev) >= 2 and sev[-1] <= RESOLVE_AT and sev[-2] <= RESOLVE_AT:
        inj["resolved"] = True
        inj["resolved_date"] = date.today().isoformat()
        return True
    return False


def open_or_update(part, severity=None, when=None, kind="injury"):
    """Open a tracked issue (kind='injury' for body-part pain, 'condition' for illness
    like fever/cold/asthma) or update the existing active one."""
    data = load()
    when = when or date.today().isoformat()
    inj = _find_active(data, part)
    if inj is None:
        inj = {"part": part, "kind": kind, "opened": when, "readings": [],
               "resolved": False, "resolved_date": None, "last_checkin": None}
        data["injuries"].append(inj)
    if severity is not None:
        inj["readings"].append({"date": when, "severity": int(severity)})
        inj["last_checkin"] = when
        _auto_resolve(inj)
    save(data)
    return inj


def log_severity(severity, part=None, when=None):
    """Record a 1-10 reading. If part is None and exactly one injury is active, use it.
    Returns (injury, resolved_bool) or (None, False) if ambiguous / nothing active."""
    data = load()
    when = when or date.today().isoformat()
    acts = active(data)
    if part:
        target = _find_active(data, part)
    elif len(acts) == 1:
        target = acts[0]
    else:
        return None, False
    if target is None:
        return None, False
    target["readings"].append({"date": when, "severity": int(severity)})
    target["last_checkin"] = when
    resolved = _auto_resolve(target)
    save(data)
    return target, resolved


def resolve(part):
    data = load()
    inj = _find_active(data, part)
    if inj:
        inj["resolved"] = True
        inj["resolved_date"] = date.today().isoformat()
        save(data)
    return inj


def resolve_all():
    data = load()
    n = 0
    for inj in data["injuries"]:
        if not inj.get("resolved"):
            inj["resolved"] = True
            inj["resolved_date"] = date.today().isoformat()
            n += 1
    save(data)
    return n


def checkin_interval_days(item):
    """Taper the check-in cadence: daily at first, then less often as it improves.
    Daily for the first 3 days; after that, based on the latest severity —
    >=5 daily, 3-4 every other day, <=2 every third day (nearly resolved)."""
    try:
        days_open = (date.today() - date.fromisoformat(item.get("opened"))).days
    except Exception:
        days_open = 0
    if days_open < 3:
        return 1
    sev = [r["severity"] for r in item.get("readings", []) if r.get("severity") is not None]
    latest = sev[-1] if sev else None
    if latest is None or latest >= 5:
        return 1
    if latest >= 3:
        return 2
    return 3


def needs_checkin(when=None):
    """Active issues due for a check-in today, honoring the tapering interval."""
    today = date.today()
    due = []
    for i in active():
        last = i.get("last_checkin")
        if last is None:
            due.append(i)
            continue
        try:
            gap = (today - date.fromisoformat(last)).days
        except Exception:
            gap = 99
        if gap >= checkin_interval_days(i):
            due.append(i)
    return due


def mark_checkin(part, when=None):
    data = load()
    when = when or date.today().isoformat()
    inj = _find_active(data, part)
    if inj:
        inj["last_checkin"] = when
        save(data)


def status():
    acts = active()
    if not acts:
        print("No active injuries. Training can run normal.")
        return
    print("=== Active issues (program around these; ease back as severity drops) ===")
    for inj in acts:
        sev = [f"{r['date'][5:]}:{r['severity']}" for r in inj.get("readings", [])]
        latest = inj["readings"][-1]["severity"] if inj.get("readings") else "?"
        kind = inj.get("kind", "injury")
        print(f"• [{kind}] {inj['part']} (opened {inj['opened']}) — latest {latest}/10, "
              f"check-in every {checkin_interval_days(inj)}d"
              + (f"  trend {' '.join(sev[-6:])}" if sev else "  (no readings yet)"))


def main():
    p = argparse.ArgumentParser(description="Track injuries + daily severity.")
    p.add_argument("--status", action="store_true")
    p.add_argument("--open", metavar="PART")
    p.add_argument("--kind", choices=["injury", "condition"], default="injury")
    p.add_argument("--severity", type=int)
    p.add_argument("--log", type=int, metavar="SEV")
    p.add_argument("--part")
    p.add_argument("--resolve", metavar="PART")
    args = p.parse_args()
    if args.open:
        inj = open_or_update(args.open, severity=args.severity, kind=args.kind)
        print(f"opened/updated [{inj.get('kind')}]: {inj['part']}")
    if args.log is not None:
        inj, res = log_severity(args.log, part=args.part)
        if inj is None:
            print("no matching active injury (specify --part).")
        else:
            print(f"logged {args.log}/10 for {inj['part']}" + (" → RESOLVED" if res else ""))
    if args.resolve:
        resolve(args.resolve); print(f"resolved: {args.resolve}")
    if args.status or not (args.open or args.log is not None or args.resolve):
        status()


if __name__ == "__main__":
    main()
