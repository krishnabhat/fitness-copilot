#!/usr/bin/env python3
"""
progress_report.py — build a periodic progress report from HEVY data: strength PRs
(est 1RM) and trends, training volume, frequency/adherence, cardio distance & best
pace, sets per muscle — plus the athlete's health metrics and what's due to measure.
Renders a self-contained HTML report and can send it to Telegram.

Usage:
  python3 progress_report.py                      # last 30 days vs prior 30, render HTML
  python3 progress_report.py --days 30 --send     # also send to Telegram
  python3 progress_report.py --note "coach summary text"   # prepend a narrative
Output: ~/.claude/skills/fitness-copilot/reports/<date>-progress.html  (path printed)
"""

import argparse
import html
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hevy_sync          # noqa: E402
import health_metrics     # noqa: E402
import activity_log       # noqa: E402
try:
    import mesocycle      # noqa: E402
except Exception:
    mesocycle = None

OUT_DIR = os.path.expanduser("~/.claude/skills/fitness-copilot/reports")


def esc(x):
    return html.escape(str(x)) if x is not None else ""


def epley(w, r):
    return w * (1 + r / 30.0)


def collect(days):
    """Return (current_window, prior_window) lists of workouts."""
    now = datetime.now(timezone.utc)
    cur, prior = [], []
    for w in hevy_sync.paginate("workouts", page_size=10, max_items=120,
                                items_key="workouts"):
        st = hevy_sync.parse_dt(w.get("start_time"))
        if not st:
            continue
        age = (now - st).days
        if age <= days:
            cur.append(w)
        elif age <= 2 * days:
            prior.append(w)
        else:
            break
    return cur, prior


def analyze(workouts, tmap):
    vol = 0.0
    muscle = {}
    lift_best = {}   # title -> best est1RM
    cardio_dist = 0.0
    cardio_time = 0.0
    best_pace = None  # (sec_per_km, label)
    for w in workouts:
        for ex in w.get("exercises", []):
            title = ex.get("title", "?")
            tid = ex.get("exercise_template_id")
            primary = (tmap.get(tid) or {}).get("primary") if tid else None
            for s in ex.get("sets", []):
                if s.get("type") == "warmup":
                    continue
                wt, reps = s.get("weight_kg"), s.get("reps")
                if wt is not None and reps is not None:
                    vol += wt * reps
                    if wt > 0:
                        e = epley(wt, reps)
                        if title not in lift_best or e > lift_best[title]:
                            lift_best[title] = e
                    if primary:
                        muscle[primary] = muscle.get(primary, 0) + 1
                dist, dur = s.get("distance_meters"), s.get("duration_seconds")
                if dist:
                    cardio_dist += dist
                    if dur:
                        cardio_time += dur
                        pace = dur / (dist / 1000.0)  # sec per km
                        if best_pace is None or pace < best_pace[0]:
                            best_pace = (pace, title)
                elif dur:
                    cardio_time += dur
    return {"volume": vol, "muscle": muscle, "lift_best": lift_best,
            "cardio_dist": cardio_dist, "cardio_time": cardio_time, "best_pace": best_pace}


def fmt_vol(kg):
    lb = kg * 2.20462
    if hevy_sync.UNITS == "lb":
        return f"{lb:,.0f} lb"
    return f"{kg:,.0f} kg"


def pct(cur, prior):
    if not prior:
        return ""
    d = (cur - prior) / prior * 100
    arrow = "▲" if d > 1 else ("▼" if d < -1 else "▬")
    return f" {arrow} {d:+.0f}% vs prior"


def fmt_pace(sec_per_km):
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}/km"


CSS = """
:root{--bg:#0f1115;--card:#171a21;--card2:#1d212b;--ink:#e8eaed;--mut:#9aa3b2;--line:#2a2f3a;--acc:#4ade80;--acc2:#38bdf8;--warn:#fbbf24;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:28px 20px 80px}
header{border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:8px}
.kicker{color:var(--acc);font-weight:700;letter-spacing:.08em;text-transform:uppercase;font-size:12px}
h1{font-size:26px;margin:6px 0 4px}.subhead{color:var(--mut);font-size:14px}
.note{background:var(--card);border-left:3px solid var(--acc);padding:12px 16px;border-radius:8px;margin:18px 0;color:#cfd5df;font-size:14.5px}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin:16px 0}
h2{font-size:15px;margin:0 0 12px;color:var(--acc2);letter-spacing:.02em}
.stat{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.stat .s{background:var(--card2);border-radius:10px;padding:10px 12px}
.stat .s b{display:block;font-size:22px}.stat .s span{color:var(--mut);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{text-align:left;padding:6px 4px;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase}
.up{color:var(--acc)}.down{color:#f87171}.flat{color:var(--mut)}
.due{border-color:var(--warn)}.due h2{color:var(--warn)}
.due ul{margin:0;padding-left:18px}
.disclaimer{color:var(--mut);font-size:12px;margin-top:24px;border-top:1px solid var(--line);padding-top:14px}
@media print{body{background:#fff;color:#111}section,.note,.stat .s{background:#fff;border-color:#ddd}}
"""


def split_acts(days):
    """Activities from the unified local log, split into current/prior windows."""
    from datetime import date, timedelta
    t = date.today()
    cur_lo = (t - timedelta(days=days)).isoformat()
    prior_lo = (t - timedelta(days=2 * days)).isoformat()
    cur, prior = [], []
    for e in activity_log.read_all():
        d = e.get("date") or ""
        if d >= cur_lo:
            cur.append(e)
        elif d >= prior_lo:
            prior.append(e)
    return cur, prior


def build_html(days, total_cur, total_prior, ca, pa, note, activities):
    freq = total_cur / (days / 7.0)
    pfreq = total_prior / (days / 7.0) if total_prior else 0
    # strength PR table: lifts present in current window
    rows = ""
    for title in sorted(ca["lift_best"], key=lambda t: -ca["lift_best"][t]):
        cur_e = ca["lift_best"][title]
        prior_e = pa["lift_best"].get(title)
        if prior_e:
            d = (cur_e - prior_e) / prior_e * 100
            cls = "up" if d > 1 else ("down" if d < -1 else "flat")
            ch = f'<span class="{cls}">{d:+.0f}%</span>'
        else:
            ch = '<span class="flat">new</span>'
        rows += (f"<tr><td>{esc(title)}</td><td>{esc(fmt_vol(cur_e))}</td>"
                 f"<td>{ch}</td></tr>")
    strength = (f"<table><tr><th>Lift</th><th>Est 1RM</th><th>Δ</th></tr>{rows}</table>"
                if rows else "<p style='color:#9aa3b2'>No strength PRs logged in this window.</p>")

    muscle = ca["muscle"]
    msum = "  ".join(f"{m} {n}" for m, n in sorted(muscle.items(), key=lambda x: -x[1]))

    cardio = ""
    if ca["cardio_dist"] or ca["cardio_time"]:
        km = ca["cardio_dist"] / 1000.0
        mins = ca["cardio_time"] / 60.0
        cardio = f"<div class='stat'><div class='s'><b>{km:.1f} km</b><span>distance</span></div>"
        cardio += f"<div class='s'><b>{mins:.0f} min</b><span>moving time</span></div>"
        if ca["best_pace"]:
            cardio += f"<div class='s'><b>{fmt_pace(ca['best_pace'][0])}</b><span>best pace</span></div>"
        cardio += "</div>"
    else:
        cardio = "<p style='color:#9aa3b2'>No cardio with distance/time logged in this window.</p>"

    # health metrics + due
    data = health_metrics.load()
    hm_lines = []
    b = health_metrics.bmi(data)
    if data.get("height_cm"):
        hm_lines.append(f"Height {data['height_cm']} cm" + (f" · BMI {b}" if b else ""))
    for key, series in data.get("metrics", {}).items():
        label, unit = health_metrics.KNOWN.get(key, (key, ""))[:2]
        latest = series[-1]
        hm_lines.append(f"{label}: {latest['value']} {unit} ({latest['date']})")
    hm_html = "<br>".join(esc(x) for x in hm_lines) if hm_lines else "No metrics logged yet."

    overdue = health_metrics.due(data, verbose=False)
    if overdue:
        items = "".join(
            f"<li>{esc(l)} — " + ("never logged" if a is None else f"{a}d ago") +
            f" (every {c}d)</li>" for l, a, c in overdue)
        due_html = f'<section class="due"><h2>📋 Time to measure / update</h2><ul>{items}</ul></section>'
    else:
        due_html = '<section class="due"><h2>📋 Measurements</h2><p>All up to date ✅</p></section>'

    meso_html = ""
    if mesocycle:
        st = mesocycle.load_state()
        if "start_date" in st:
            meso_html = f"<section><h2>Periodization</h2><p style='font-size:14px'>Mesocycle started {esc(st['start_date'])} — see daily plans for current phase.</p></section>"

    if activities:
        rows = ""
        for e in sorted(activities, key=lambda x: x.get("date", ""), reverse=True):
            extra = []
            if e.get("distance_km"):
                extra.append(f"{e['distance_km']} km")
            if e.get("duration_min"):
                extra.append(f"{int(e['duration_min'])} min")
            rows += (f"<tr><td>{esc(e.get('date','?'))}</td><td>{esc(e.get('source','?'))}</td>"
                     f"<td>{esc(e.get('type','?'))}</td><td>{esc(e.get('title',''))}</td>"
                     f"<td>{esc(', '.join(extra))}</td></tr>")
        activities_html = (f"<table><tr><th>Date</th><th>Source</th><th>Type</th>"
                           f"<th>What</th><th></th></tr>{rows}</table>")
    else:
        activities_html = "<p style='color:#9aa3b2'>No activities logged in this window.</p>"

    note_html = f'<div class="note"><b>Coach\'s notes:</b> {esc(note)}</div>' if note else ""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Progress report</title><style>{CSS}</style></head>
<body><div class="wrap">
<header><div class="kicker">Fitness Copilot · Progress report</div>
<h1>Last {days} days</h1><div class="subhead">Strength · volume · cardio · body metrics</div></header>
{note_html}
<section><h2>Training</h2><div class="stat">
  <div class="s"><b>{total_cur}</b><span>activities{esc(pct(total_cur, total_prior))}</span></div>
  <div class="s"><b>{freq:.1f}/wk</b><span>frequency{esc(pct(freq, pfreq))}</span></div>
  <div class="s"><b>{fmt_vol(ca['volume'])}</b><span>strength volume{esc(pct(ca['volume'], pa['volume']))}</span></div>
</div></section>
<section><h2>All logged activities ({len(activities)})</h2>{activities_html}</section>
<section><h2>Strength PRs &amp; trends (est 1RM)</h2>{strength}</section>
<section><h2>Cardio</h2>{cardio}</section>
<section><h2>Sets per muscle (this window)</h2><p style="font-size:14px">{esc(msum) or '—'}</p></section>
<section><h2>Body &amp; health metrics</h2><p style="font-size:14px">{hm_html}</p></section>
{due_html}{meso_html}
<div class="disclaimer">Generated by the fitness-copilot skill from your HEVY data and logged metrics.
Trends are estimates (est 1RM via Epley). Coaching guidance, not medical advice.</div>
</div></body></html>"""


def main():
    p = argparse.ArgumentParser(description="Build a progress report from HEVY + metrics.")
    p.add_argument("--days", type=int, default=30, help="window size in days (default 30)")
    p.add_argument("--note", help="coach narrative to prepend")
    p.add_argument("--send", action="store_true", help="send the report to Telegram")
    p.add_argument("--out", help="output HTML path")
    args = p.parse_args()

    hevy_sync.UNITS = hevy_sync.detect_units()
    activity_log.sync_hevy()   # ensure the local log reflects the latest HEVY workouts
    tmap = hevy_sync.load_template_map()
    cur, prior = collect(args.days)
    ca, pa = analyze(cur, tmap), analyze(prior, tmap)

    # Fold non-HEVY logged activities (e.g. runs texted via Telegram) into the picture.
    acts_cur, acts_prior = split_acts(args.days)
    for e in acts_cur:
        if e.get("source") == "hevy":
            continue
        d, du = e.get("distance_km"), e.get("duration_min")
        if d:
            ca["cardio_dist"] += d * 1000
        if du:
            ca["cardio_time"] += du * 60
        if d and du:
            pace = (du * 60) / d
            if ca["best_pace"] is None or pace < ca["best_pace"][0]:
                ca["best_pace"] = (pace, e.get("title"))

    doc = build_html(args.days, len(acts_cur), len(acts_prior), ca, pa, args.note, acts_cur)

    out = args.out
    if not out:
        os.makedirs(OUT_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d")
        out = os.path.join(OUT_DIR, f"{stamp}-progress.html")
    out = os.path.expanduser(out)
    with open(out, "w") as f:
        f.write(doc)
    print(out)

    if args.send:
        import notify_telegram
        creds = notify_telegram.load_creds()
        notify_telegram.send_document(
            creds, out, args.note or f"📊 Your {args.days}-day progress report.")


if __name__ == "__main__":
    main()
