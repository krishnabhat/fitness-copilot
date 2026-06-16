#!/usr/bin/env python3
"""
build_week.py — render a week-at-a-glance training plan as a self-contained HTML
page (companion to build_workout.py, which renders a single detailed session).

The weekly plan is a roadmap — what's coming each day — not full prescriptions;
the nightly planner builds each day's detailed session + HEVY routine.

Usage:
  python3 build_week.py week.json            # render from a file
  cat week.json | python3 build_week.py -    # from stdin
  python3 build_week.py week.json --out ~/week.html
Output defaults to ~/.claude/skills/fitness-copilot/plans/<week_start>-week.html
and the path is printed on the last line.

JSON spec (all optional except days[].day):
{
  "title": "Week of Jun 16–22",
  "week_start": "2026-06-16",
  "focus": "Recomp — balanced split, legs prioritized",
  "overview": "1–2 line summary of the week's emphasis and any deload/ramp note",
  "days": [
    {"day": "Mon", "date": "2026-06-16", "focus": "Lower Body + Core",
     "type": "strength", "summary": "Goblet squat, light DB RDL, reverse lunge, McGill core",
     "duration_min": 45},
    {"day": "Tue", "focus": "Zone 2 cardio", "type": "cardio",
     "summary": "40 min easy hills or bike, conversational pace"},
    {"day": "Sun", "focus": "Rest / mobility", "type": "rest", "summary": "Optional yoga flow"}
  ],
  "notes": ["weekly volume target", "back/cardiac reminders", "nutrition focus"]
}
"""

import argparse
import html
import json
import os
import re
import sys

OUT_DIR = os.path.expanduser("~/.claude/skills/fitness-copilot/plans")

TYPE_COLOR = {
    "strength": "#4ade80", "hypertrophy": "#4ade80",
    "cardio": "#38bdf8", "endurance": "#38bdf8", "hiit": "#fbbf24",
    "conditioning": "#fbbf24", "mobility": "#a78bfa", "yoga": "#a78bfa",
    "rest": "#9aa3b2", "recovery": "#9aa3b2",
}


def esc(x):
    return html.escape(str(x)) if x is not None else ""


def slugify(t):
    return re.sub(r"[^a-z0-9]+", "-", (t or "week").lower()).strip("-") or "week"


CSS = """
:root{--bg:#0f1115;--card:#171a21;--card2:#1d212b;--ink:#e8eaed;--mut:#9aa3b2;--line:#2a2f3a;--acc:#4ade80;--acc2:#38bdf8;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:820px;margin:0 auto;padding:28px 20px 80px;}
header{border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:8px;}
.kicker{color:var(--acc);font-weight:700;letter-spacing:.08em;text-transform:uppercase;font-size:12px;}
h1{font-size:26px;margin:6px 0 4px;}.subhead{color:var(--mut);font-size:14px;}
.overview{background:var(--card);border-left:3px solid var(--acc);padding:12px 16px;border-radius:8px;margin:18px 0;color:#cfd5df;font-size:14.5px;}
.day{display:flex;gap:14px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:10px 0;align-items:flex-start;}
.daytag{min-width:54px;font-weight:700;font-size:13px;color:var(--ink);text-align:center;}
.daytag .date{display:block;color:var(--mut);font-weight:400;font-size:11px;margin-top:2px;}
.bar{width:4px;align-self:stretch;border-radius:3px;background:var(--mut);}
.body{flex:1;}
.focus{font-weight:650;font-size:15.5px;}
.badge{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;border-radius:5px;padding:1px 7px;margin-left:8px;color:#04222e;font-weight:700;}
.summary{color:var(--mut);font-size:13.5px;margin-top:3px;}
.dur{color:var(--acc2);font-size:12px;margin-top:4px;}
.notes{background:var(--card2);border-radius:10px;padding:14px 16px;margin-top:18px;}
.notes h2{font-size:14px;color:var(--acc2);margin:0 0 8px;}
.notes ul{margin:0;padding-left:18px;color:#cfd5df;font-size:13.5px;}
.disclaimer{color:var(--mut);font-size:12px;margin-top:24px;border-top:1px solid var(--line);padding-top:14px;}
@media print{body{background:#fff;color:#111}.day,.overview,.notes{background:#fff;border-color:#ddd}}
"""


def render_day(d):
    t = (d.get("type") or "").lower()
    color = TYPE_COLOR.get(t, "#9aa3b2")
    badge = (f'<span class="badge" style="background:{color}">{esc(t)}</span>'
             if t else "")
    date = f'<span class="date">{esc(d["date"])}</span>' if d.get("date") else ""
    dur = (f'<div class="dur">~{esc(d["duration_min"])} min</div>'
           if d.get("duration_min") else "")
    summary = f'<div class="summary">{esc(d["summary"])}</div>' if d.get("summary") else ""
    return f"""
    <div class="day">
      <div class="daytag">{esc(d.get('day',''))}{date}</div>
      <div class="bar" style="background:{color}"></div>
      <div class="body">
        <div class="focus">{esc(d.get('focus','—'))}{badge}</div>
        {summary}{dur}
      </div>
    </div>"""


def build_html(spec):
    title = esc(spec.get("title", "Training Week"))
    focus = esc(spec.get("focus", ""))
    overview = (f'<div class="overview">{esc(spec["overview"])}</div>'
                if spec.get("overview") else "")
    days = "".join(render_day(d) for d in spec.get("days", []))
    notes = ""
    if spec.get("notes"):
        items = "".join(f"<li>{esc(n)}</li>" for n in spec["notes"])
        notes = f'<div class="notes"><h2>Notes for the week</h2><ul>{items}</ul></div>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{CSS}</style></head>
<body><div class="wrap">
  <header><div class="kicker">Fitness Copilot · Week ahead</div>
  <h1>{title}</h1><div class="subhead">{focus}</div></header>
  {overview}{days}{notes}
  <div class="disclaimer">Weekly roadmap from the fitness-copilot skill — each day's
  full session + HEVY routine is built the night before. Coaching guidance, not medical advice.</div>
</div></body></html>"""


def main():
    p = argparse.ArgumentParser(description="Render a weekly plan to HTML.")
    p.add_argument("spec", help="path to JSON spec, or '-' for stdin")
    p.add_argument("--out", help="output HTML path")
    args = p.parse_args()
    raw = sys.stdin.read() if args.spec == "-" else open(args.spec).read()
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: spec is not valid JSON: {e}")
    out = args.out
    if not out:
        os.makedirs(OUT_DIR, exist_ok=True)
        ws = spec.get("week_start", "week")
        out = os.path.join(OUT_DIR, f"{ws}-{slugify(spec.get('title'))}.html")
    out = os.path.expanduser(out)
    with open(out, "w") as f:
        f.write(build_html(spec))
    print(out)


if __name__ == "__main__":
    main()
