#!/usr/bin/env python3
"""
build_workout.py — render a workout session as a clean, self-contained HTML page.

The skill produces a JSON workout spec and pipes it here; this script renders a
styled, printable, mobile-friendly HTML file. Every exercise gets a "how to"
YouTube link (auto-generated as a form-search link unless an explicit URL is
provided in the spec). Warm-up, cool-down, and rest times are always rendered.

Usage:
  python3 build_workout.py spec.json                 # render from a file
  cat spec.json | python3 build_workout.py -         # render from stdin
  python3 build_workout.py spec.json --out ~/wk.html # custom output path
  python3 build_workout.py spec.json --open          # also open in browser (macOS)

Output defaults to:  ~/.claude/skills/fitness-copilot/workouts/<date>-<slug>.html
The output path is printed on the last line.

JSON spec schema (all fields optional except exercise `name`):
{
  "title": "Lower Body Strength",
  "date": "2026-06-15",                 # defaults to today if omitted
  "focus": "Squat-focused strength",
  "why": "1-2 lines tying to history, recovery, goal, constraints",
  "duration_min": 60,
  "location": "Full commercial gym",    # or "Garage gym", "Home minimal", "Travel/bodyweight"
  "equipment": ["barbell", "rack", "dumbbells"],
  "warmup":   [ {"name": "Bike", "detail": "3 min easy Z1"}, ... ],
  "blocks": [
    {
      "label": "Main work",
      "rest_between_exercises_sec": 120,           # optional
      "note": "optional block note (e.g. 'superset A1+A2')",
      "exercises": [
        {
          "name": "Barbell Back Squat",
          "sets": 4, "reps": "5", "load": "80% 1RM / RPE 8",
          "rest_sec": 180,
          "tempo": "3-0-1",                         # optional
          "superset": "A1",                          # optional badge
          "cues": ["brace before you descend", "knees track over toes"],
          "target": "add 2.5 kg vs last time if all reps stayed <= RPE 8",
          "video": "https://www.youtube.com/watch?v=..."   # optional; auto if absent
        }
      ]
    }
  ],
  "conditioning": { "label": "Conditioning", "exercises": [...] },   # optional block
  "cooldown": [ {"name": "Couch stretch", "detail": "60s/side"} ],
  "coaching_cues": ["overall cue 1", "cue 2"],
  "fuel": { "pre": "...", "intra": "...", "post": "..." },
  "watch_outs": ["stop if sharp knee pain", ...],
  "progression_rule": "how to know whether to go up next time"
}
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.parse

OUT_DIR = os.path.expanduser("~/.claude/skills/fitness-copilot/workouts")


def slugify(text):
    text = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").lower()).strip("-")
    return text or "workout"


def youtube_link(exercise_name, explicit=None):
    """Explicit URL if given, else a YouTube search for proper-form how-to."""
    if explicit:
        return explicit
    q = urllib.parse.quote_plus(f"how to {exercise_name} proper form technique")
    return f"https://www.youtube.com/results?search_query={q}"


def fmt_rest(seconds):
    if seconds is None:
        return None
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    if s < 60:
        return f"{s}s"
    m, r = divmod(s, 60)
    return f"{m}:{r:02d} min" if r else f"{m} min"


def esc(x):
    return html.escape(str(x)) if x is not None else ""


def render_simple_list(items):
    """For warm-up / cool-down: name + detail rows."""
    rows = []
    for it in items or []:
        if isinstance(it, str):
            rows.append(f'<li><span class="ex-name">{esc(it)}</span></li>')
            continue
        name = esc(it.get("name", ""))
        detail = esc(it.get("detail", ""))
        link = youtube_link(it.get("name", ""), it.get("video"))
        video = (f' <a class="yt" href="{esc(link)}" target="_blank" '
                 f'rel="noopener">▶ how-to</a>')
        rows.append(
            f'<li><span class="ex-name">{name}</span>'
            f'{" — " + detail if detail else ""}{video}</li>'
        )
    return "<ul class='plain'>" + "".join(rows) + "</ul>"


def render_exercise(ex):
    name = esc(ex.get("name", "Exercise"))
    link = youtube_link(ex.get("name", ""), ex.get("video"))
    badge = ""
    if ex.get("superset"):
        badge = f'<span class="badge">{esc(ex["superset"])}</span>'
    # prescription line
    presc = []
    if ex.get("sets") is not None and ex.get("reps") is not None:
        presc.append(f'<b>{esc(ex["sets"])} × {esc(ex["reps"])}</b>')
    elif ex.get("reps") is not None:
        presc.append(f'<b>{esc(ex["reps"])} reps</b>')
    elif ex.get("sets") is not None:
        presc.append(f'<b>{esc(ex["sets"])} sets</b>')
    if ex.get("load"):
        presc.append(f'@ {esc(ex["load"])}')
    if ex.get("tempo"):
        presc.append(f'tempo {esc(ex["tempo"])}')
    presc_line = " ".join(presc)

    meta = []
    rest = fmt_rest(ex.get("rest_sec"))
    if rest:
        meta.append(f'<span class="pill rest">⏱ rest {rest}</span>')
    if ex.get("target"):
        meta.append(f'<span class="pill target">🎯 {esc(ex["target"])}</span>')
    meta_line = " ".join(meta)

    cues = ""
    if ex.get("cues"):
        items = "".join(f"<li>{esc(c)}</li>" for c in ex["cues"])
        cues = f'<ul class="cues">{items}</ul>'

    return f"""
    <div class="exercise">
      <div class="ex-head">
        <span class="ex-name">{name}</span>{badge}
        <a class="yt" href="{esc(link)}" target="_blank" rel="noopener">▶ how-to</a>
      </div>
      <div class="presc">{presc_line}</div>
      <div class="meta">{meta_line}</div>
      {cues}
    </div>"""


def render_block(block):
    if not block:
        return ""
    label = esc(block.get("label", "Block"))
    note = block.get("note")
    rbe = fmt_rest(block.get("rest_between_exercises_sec"))
    sub = []
    if note:
        sub.append(esc(note))
    if rbe:
        sub.append(f"rest between exercises: {rbe}")
    subline = (f'<div class="block-note">{" · ".join(sub)}</div>'
               if sub else "")
    exs = "".join(render_exercise(e) for e in block.get("exercises", []))
    return f"""
    <section class="block">
      <h2>{label}</h2>
      {subline}
      {exs}
    </section>"""


CSS = """
:root{--bg:#0f1115;--card:#171a21;--card2:#1d212b;--ink:#e8eaed;--mut:#9aa3b2;
--acc:#4ade80;--acc2:#38bdf8;--warn:#fbbf24;--danger:#f87171;--line:#2a2f3a;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:820px;margin:0 auto;padding:28px 20px 80px;}
header.hero{border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:8px;}
.kicker{color:var(--acc);font-weight:700;letter-spacing:.08em;text-transform:uppercase;font-size:12px;}
h1{font-size:28px;margin:6px 0 4px;line-height:1.15;}
.subhead{color:var(--mut);font-size:14px;}
.chips{margin:12px 0 0;display:flex;flex-wrap:wrap;gap:8px;}
.chip{background:var(--card2);border:1px solid var(--line);color:var(--ink);
border-radius:999px;padding:4px 11px;font-size:12.5px;}
.chip.loc{border-color:var(--acc2);color:var(--acc2);}
.why{background:var(--card);border-left:3px solid var(--acc);padding:12px 16px;
border-radius:8px;margin:18px 0;color:#cfd5df;font-size:14.5px;}
section.block,section.pane{background:var(--card);border:1px solid var(--line);
border-radius:14px;padding:16px 18px;margin:16px 0;}
h2{font-size:16px;margin:0 0 10px;color:var(--acc2);letter-spacing:.02em;}
.block-note{color:var(--mut);font-size:13px;margin:-4px 0 12px;}
.exercise{border-top:1px solid var(--line);padding:12px 0;}
.exercise:first-of-type{border-top:none;padding-top:2px;}
.ex-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.ex-name{font-weight:650;font-size:16px;}
.badge{background:var(--acc2);color:#04222e;font-weight:700;font-size:11px;
border-radius:5px;padding:1px 7px;}
.yt{margin-left:auto;color:#fff;background:#c4302b;text-decoration:none;
font-size:12px;font-weight:600;padding:3px 9px;border-radius:6px;white-space:nowrap;}
.yt:hover{filter:brightness(1.1);}
.presc{margin:4px 0 2px;font-size:15px;}
.presc b{color:var(--acc);}
.meta{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 2px;}
.pill{font-size:12px;border-radius:6px;padding:2px 8px;border:1px solid var(--line);}
.pill.rest{color:var(--acc);} .pill.target{color:var(--warn);}
ul.cues{margin:8px 0 2px;padding-left:18px;color:var(--mut);font-size:13.5px;}
ul.plain{list-style:none;margin:0;padding:0;}
ul.plain li{padding:7px 0;border-top:1px solid var(--line);display:flex;
align-items:center;gap:8px;flex-wrap:wrap;}
ul.plain li:first-child{border-top:none;}
ul.plain .yt{margin-left:auto;}
.fuel{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;}
.fuel .f{background:var(--card2);border-radius:10px;padding:10px 12px;font-size:13.5px;}
.fuel .f b{color:var(--acc2);display:block;margin-bottom:2px;font-size:12px;text-transform:uppercase;letter-spacing:.05em;}
.watch{border-color:var(--danger);}
.watch h2{color:var(--danger);}
.watch ul{margin:0;padding-left:18px;}
.disclaimer{color:var(--mut);font-size:12px;margin-top:24px;border-top:1px solid var(--line);padding-top:14px;}
@media print{body{background:#fff;color:#111;}.wrap{max-width:100%;}
section.block,section.pane,.why,.fuel .f{background:#fff;border-color:#ddd;}
.yt{background:#c4302b;}h1,h2{color:#111;}.kicker{color:#1a7f37;}}
"""


def build_html(spec):
    title = esc(spec.get("title", "Workout"))
    date = esc(spec.get("date", "today"))
    focus = esc(spec.get("focus", ""))
    chips = []
    if spec.get("duration_min"):
        chips.append(f'<span class="chip">⏳ ~{esc(spec["duration_min"])} min</span>')
    if spec.get("location"):
        chips.append(f'<span class="chip loc">🏋 {esc(spec["location"])}</span>')
    for eq in spec.get("equipment", []) or []:
        chips.append(f'<span class="chip">{esc(eq)}</span>')
    chips_html = ("<div class='chips'>" + "".join(chips) + "</div>") if chips else ""

    why = (f'<div class="why"><b>Why today:</b> {esc(spec["why"])}</div>'
           if spec.get("why") else "")

    panes = []
    if spec.get("warmup"):
        panes.append(
            f'<section class="pane"><h2>Warm-up</h2>'
            f'{render_simple_list(spec["warmup"])}</section>'
        )
    for block in spec.get("blocks", []) or []:
        panes.append(render_block(block))
    if spec.get("conditioning"):
        panes.append(render_block(spec["conditioning"]))
    if spec.get("cooldown"):
        panes.append(
            f'<section class="pane"><h2>Cool-down &amp; mobility</h2>'
            f'{render_simple_list(spec["cooldown"])}</section>'
        )

    if spec.get("coaching_cues"):
        items = "".join(f"<li>{esc(c)}</li>" for c in spec["coaching_cues"])
        panes.append(
            f'<section class="pane"><h2>Coaching cues</h2>'
            f'<ul class="cues" style="font-size:14px">{items}</ul></section>'
        )

    if spec.get("progression_rule"):
        panes.append(
            f'<section class="pane"><h2>Progression rule</h2>'
            f'<div style="font-size:14px;color:#cfd5df">'
            f'{esc(spec["progression_rule"])}</div></section>'
        )

    if spec.get("fuel"):
        f = spec["fuel"]
        cells = ""
        for k, lbl in (("pre", "Pre"), ("intra", "Intra"), ("post", "Post")):
            if f.get(k):
                cells += f'<div class="f"><b>{lbl}</b>{esc(f[k])}</div>'
        if cells:
            panes.append(
                f'<section class="pane"><h2>Fuel</h2>'
                f'<div class="fuel">{cells}</div></section>'
            )

    if spec.get("watch_outs"):
        items = "".join(f"<li>{esc(w)}</li>" for w in spec["watch_outs"])
        panes.append(
            f'<section class="pane watch"><h2>⚠ Watch-outs — stop if…</h2>'
            f'<ul>{items}</ul></section>'
        )

    body = "\n".join(panes)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — {date}</title><style>{CSS}</style></head>
<body><div class="wrap">
  <header class="hero">
    <div class="kicker">Fitness Copilot · {date}</div>
    <h1>{title}</h1>
    <div class="subhead">{focus}</div>
    {chips_html}
  </header>
  {why}
  {body}
  <div class="disclaimer">Generated by the fitness-copilot skill. Coaching guidance,
  not medical advice — stop and seek care for any red-flag symptom (chest pain,
  faintness, sharp/radiating pain, etc.).</div>
</div></body></html>"""


def main():
    p = argparse.ArgumentParser(description="Render a workout spec to HTML.")
    p.add_argument("spec", help="path to JSON spec, or '-' for stdin")
    p.add_argument("--out", help="output HTML path (default: workouts/<date>-<slug>.html)")
    p.add_argument("--open", action="store_true", help="open the file (macOS 'open')")
    args = p.parse_args()

    raw = sys.stdin.read() if args.spec == "-" else open(args.spec).read()
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: spec is not valid JSON: {e}")

    htmldoc = build_html(spec)

    out = args.out
    if not out:
        os.makedirs(OUT_DIR, exist_ok=True)
        slug = slugify(spec.get("title") or spec.get("focus"))
        date = spec.get("date", "session")
        out = os.path.join(OUT_DIR, f"{date}-{slug}.html")
    out = os.path.expanduser(out)
    with open(out, "w") as f:
        f.write(htmldoc)

    if args.open:
        try:
            subprocess.run(["open", out], check=False)
        except Exception:
            pass
    print(out)


if __name__ == "__main__":
    main()
