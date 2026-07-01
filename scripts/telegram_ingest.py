#!/usr/bin/env python3
"""
telegram_ingest.py — poll the Telegram bot for messages the athlete sends, parse
each into an activity, log it to the canonical activity_log, and reply to confirm.

This makes the bot two-way: text it "ran 5k in 28 min" or "did 30 min yoga" and it
gets logged just like a HEVY workout. Anything it can't confidently parse is still
logged (type=other) with the raw text, so nothing is lost.

Runs headless (stdlib only). A launchd job polls it every ~15 min; you can also run:
  python3 telegram_ingest.py --poll       # process any new messages once
  python3 telegram_ingest.py --poll --quiet

Offset (last processed update) is stored in profile/.telegram_offset.
"""

import argparse
import glob
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import json
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notify_telegram   # noqa: E402  (load_creds, send_message)
import activity_log      # noqa: E402
import notes             # noqa: E402

PAIN_WORDS = ["pain", "hurt", "sore", "soreness", "ache", "aching", "tweak",
              "stiff", "strain", "sprain", "injured", "injury", "tight",
              "pulled", "spasm", "cramp", "throb"]
STATUS_WORDS = ["tired", "exhausted", "fatigued", "didn't sleep", "didnt sleep",
                "stressed", "sick", "ill", "flu", "run down", "rundown",
                "low energy", "drained", "burnt out", "burned out", "wiped",
                # illness / under-the-weather
                "cold", "cough", "congested", "congestion", "sinus", "sore throat",
                "throat", "headache", "migraine", "nausea", "nauseous", "chills",
                "under the weather", "achy", "fever", "feverish", "sniffles", "runny nose"]
RECOVERY_WORDS = ["recovered", "recovering", "feeling better", "better now", "back to normal",
                  "all better", "on the mend", "over the cold", "over it", "fully recovered",
                  "feeling good again", "back to full", "much better", "back to normal now",
                  "cold is gone", "feel great again"]
RED_FLAG_WORDS = ["sharp", "radiating", "shooting", "numb", "tingl", "chest pain",
                  "can't move", "cant move", "severe", "dizzy", "faint",
                  "short of breath", "shortness of breath"]
BODY_PARTS = ["lower back", "upper back", "back", "neck", "shoulder", "knee",
              "hip", "ankle", "wrist", "elbow", "hamstring", "quad", "calf",
              "groin", "spine", "disc", "achilles", "foot", "leg", "thigh"]
WORKOUT_SIGNAL = ["workout", "session", "trained", "exercised", "gym", "wod"]

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKOUTS_DIR = os.path.expanduser("~/.claude/skills/fitness-copilot/workouts")
PLANS_DIR = os.path.expanduser("~/.claude/skills/fitness-copilot/plans")
COMMAND_VERBS = ["show", "send", "get me", "give me", "pull up", "see my",
                 "what's my", "what is my", "whats my", "display", "fetch"]
FOCUS_WORDS = ["push", "pull", "legs", "leg", "lower", "upper", "full body",
               "strength", "cardio", "run", "hiit", "yoga", "mobility", "conditioning"]


PLAN_VERBS = ["plan", "generate", "create", "build me", "make me", "design", "give me a new"]


def is_command(low):
    return any(v in low for v in COMMAND_VERBS)


def is_plan_request(low):
    # "I plan to rest" / "planning to ..." are intent statements, not requests.
    if "plan to " in low or "planning to" in low:
        return False
    return any(v in low for v in PLAN_VERBS) and (
        any(w in low for w in ["workout", "session", "day", "routine", "training"])
        or any(fw in low for fw in FOCUS_WORDS) or "today" in low or "tomorrow" in low)


def spawn_plan(request, creds):
    """Kick off headless generation in the background; reply immediately."""
    try:
        subprocess.Popen(["/bin/zsh", os.path.join(SCRIPTS_DIR, "plan_on_demand.sh"), request],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        notify_telegram.send_message(creds, "On it. Planning that now. I'll send it here in "
                                     "a couple of minutes. 💪")
    except Exception:
        notify_telegram.send_message(creds, "Couldn't start planning just now.")


def _title_from_file(path):
    name = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", os.path.basename(path)[:-5])
    return name.replace("-", " ").strip().title() or "Workout"


def _newest(paths):
    return max(paths, key=os.path.getmtime) if paths else None


def _parse_days(low, default=14):
    m = re.search(r"(\d+)\s*(day|week|month)", low)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return n * (30 if "month" in unit else 7 if "week" in unit else 1)
    if "month" in low:
        return 30
    if "week" in low:
        return 7
    return default


def handle_command(low, creds):
    """Answer a query like 'show me tomorrow's workout' by SENDING the rendered
    HTML / report instead of logging. Returns True if it handled the message."""
    # progress report / stats
    if any(w in low for w in ["progress", "report", "stats", "how am i", "how'm i"]):
        notify_telegram.send_message(creds, "Pulling your progress report…")
        try:
            subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "progress_report.py"),
                            "--days", "30", "--send"], timeout=180, capture_output=True)
        except Exception:
            notify_telegram.send_message(creds, "Couldn't generate the report just now.")
        return True
    # unified history / log (checked before "week" so "last 2 weeks" doesn't hit week-plan)
    if "history" in low or " log" in low or low.startswith("log") or (
            any(w in low for w in ["last", "past", "previous", "recent"])
            and any(u in low for u in ["week", "day", "month"])):
        days = _parse_days(low, default=14)
        try:
            out = subprocess.run([sys.executable, os.path.join(SCRIPTS_DIR, "activity_log.py"),
                                  "--sync-hevy", "--history", "--days", str(days)],
                                 timeout=60, capture_output=True, text=True).stdout
        except Exception:
            out = ""
        # drop the noisy sync line; keep the history block
        msg = "\n".join(l for l in out.splitlines() if "HEVY sync" not in l).strip()
        notify_telegram.send_message(creds, msg[:4000] or "No history found.")
        return True
    # weekly plan
    if "week" in low:
        f = _newest(glob.glob(os.path.join(PLANS_DIR, "*.html")))
        notify_telegram.send_document(creds, f, "🗓️ Your week ahead.") if f else \
            notify_telegram.send_message(creds, "No weekly plan rendered yet — those generate on Fridays.")
        return True
    # a specific workout / session
    if (any(w in low for w in ["workout", "session", "routine", "training", "exercise"])
            or "today" in low or "tomorrow" in low
            or any(fw in low for fw in FOCUS_WORDS)):
        files = glob.glob(os.path.join(WORKOUTS_DIR, "*.html"))
        if not files:
            notify_telegram.send_message(creds, "No workout is rendered yet. Ask your coach "
                "to plan one, or it'll be ready after tonight's auto-plan.")
            return True
        cand = files
        focus = next((fw for fw in FOCUS_WORDS if fw in low), None)
        if focus:
            matched = [f for f in cand if focus.replace(" ", "-") in os.path.basename(f).lower()]
            if matched:
                cand = matched
        note = None
        if "tomorrow" in low or "today" in low:
            when = "tomorrow" if "tomorrow" in low else "today"
            pref = (date.today() + (timedelta(days=1) if when == "tomorrow" else timedelta(0))).isoformat()
            dated = [f for f in cand if os.path.basename(f).startswith(pref)]
            if dated:
                cand = dated
            else:
                # No file stamped that exact day → send the latest planned session instead.
                note = f"Nothing dated {when} specifically. Here's your latest planned session:"
        chosen = _newest(cand)
        if note:
            notify_telegram.send_message(creds, note)
        notify_telegram.send_document(creds, chosen, f"💪 {_title_from_file(chosen)}")
        return True
    return False

OFFSET_FILE = os.path.expanduser("~/.claude/skills/fitness-copilot/profile/.telegram_offset")

TYPE_WORDS = [
    ("run", ["run", "ran", "jog", "jogged", "sprint"]),
    ("bike", ["bike", "biked", "cycle", "cycled", "ride", "rode", "cycling", "spin"]),
    ("walk", ["walk", "walked", "hike", "hiked"]),
    ("swim", ["swim", "swam", "swimming"]),
    ("yoga", ["yoga", "stretch", "mobility", "flow"]),
    ("hiit", ["hiit", "circuit", "metcon", "emom", "tabata", "amrap"]),
    ("strength", ["lift", "lifted", "strength", "weights", "gym", "press", "squat",
                  "deadlift", "push", "pull", "leg", "legs", "upper", "lower"]),
]


def load_offset():
    if os.path.exists(OFFSET_FILE):
        try:
            return int(open(OFFSET_FILE).read().strip())
        except Exception:
            return 0
    return 0


def save_offset(v):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(v))


def parse_activity(text):
    t = text.lower()
    atype = "other"
    for name, words in TYPE_WORDS:
        if any(re.search(rf"\b{w}\b", t) for w in words):
            atype = name
            break
    # distance (word boundaries so "min" is never read as "mi")
    dist_km = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:kms|km|k)\b", t)
    if m:
        dist_km = float(m.group(1))
    else:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:miles|mile|mi)\b", t)
        if m:
            dist_km = round(float(m.group(1)) * 1.60934, 2)
    # duration (check minutes before hours; require whole-word units)
    dur_min = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:minutes|minute|mins|min)\b", t)
    if m:
        dur_min = float(m.group(1))
    else:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours|hour|hrs|hr|h)\b", t)
        if m:
            dur_min = float(m.group(1)) * 60
    return atype, dist_km, dur_min


def confirm_text(atype, dist_km, dur_min, units_lb):
    bits = [atype]
    if dist_km:
        if units_lb:
            bits.append(f"{round(dist_km/1.60934, 2)} mi")
        else:
            bits.append(f"{dist_km} km")
    if dur_min:
        bits.append(f"{int(dur_min)} min")
    return "✅ Logged: " + ", ".join(bits) + ". Nice work 💪"


def poll(quiet=False):
    creds = notify_telegram.load_creds()
    offset = load_offset()
    url = f"https://api.telegram.org/bot{creds['token']}/getUpdates"
    params = {"timeout": 0}
    if offset:
        params["offset"] = offset + 1
    try:
        with urllib.request.urlopen(url + "?" + urllib.parse.urlencode(params), timeout=30) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        print(f"poll error: {e}")
        return 0
    if not data.get("ok"):
        print(f"getUpdates not ok: {data}")
        return 0

    logged = 0
    last_update = offset
    for upd in data.get("result", []):
        last_update = max(last_update, upd.get("update_id", 0))
        msg = upd.get("message") or upd.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if not text or chat_id != str(creds["chat_id"]):
            continue
        if text.startswith("/"):       # ignore bot commands like /start
            continue
        # "also:" / "force:" prefix overrides the duplicate guard for a genuinely
        # separate same-day session.
        override = text.lower().startswith(("also:", "force:"))
        clean = text.split(":", 1)[1].strip() if override else text
        low = clean.lower()

        # Use the message's OWN timestamp for the log date, not poll time. A text sent
        # at 11:58pm but polled after midnight (or a wake-from-sleep catch-up) must not
        # land on the wrong day and skew recency/gating/recovery logic.
        _ts = msg.get("date")
        msg_day = (datetime.fromtimestamp(_ts, tz=timezone.utc).astimezone().date().isoformat()
                   if _ts else date.today().isoformat())

        # Intent flags computed up front.
        atype, dist_km, dur_min = parse_activity(clean)
        is_pain = any(w in low for w in PAIN_WORDS)
        is_recovery = any(w in low for w in RECOVERY_WORDS)
        has_fever = "fever" in low or "feverish" in low
        is_status = is_recovery or any(w in low for w in STATUS_WORDS)
        red_flag = any(w in low for w in RED_FLAG_WORDS)
        part = next((b for b in BODY_PARTS if b in low), None)

        # Persist a pain/status note FIRST — so a mixed-intent message like
        # "plan me a workout, my knee hurts" never loses the constraint to the plan path.
        note_ack = None
        if is_pain or is_status:
            notes.append_note(clean, kind="pain" if (is_pain and not is_recovery) else "status",
                              red_flag=red_flag or has_fever, when=msg_day)
            logged += 1
            if red_flag:
                note_ack = ("⚠️ Noted — that can be serious. If it's sharp, radiating, numb, "
                    "or comes with chest pain/dizziness, please STOP and see a clinician. "
                    "I'll keep training off that area until you're cleared.")
            elif is_recovery:
                note_ack = ("Great to hear you're back 💪 I'll resume normal programming — "
                    "your next session returns to your regular plan, easing in moderate-first.")
            elif is_pain:
                note_ack = (f"Got it — noted your {part or 'pain'}. I'll program around it next "
                    "session (avoid loading that area). Tell me if it turns sharp or radiates.")
            elif has_fever:
                note_ack = ("Noted. With a fever, rest fully — no training until it's gone, then "
                    "ease back in (no HIIT/heavy right away). I'll keep sessions held until you're clear.")
            else:
                note_ack = ("Noted — I'll keep your next session easy while you recover. "
                    "Text me when you're feeling better and I'll resume as normal.")

        # Query/command (e.g. "show me tomorrow's workout") → send it. Note already stored above.
        if not override and is_command(low) and handle_command(low, creds):
            if note_ack:
                try:
                    notify_telegram.send_message(creds, note_ack)
                except Exception:
                    pass
            logged += 1
            continue
        # Plan request (e.g. "plan me a leg day") → generate in the background. Note stored above.
        if not override and is_plan_request(low):
            spawn_plan(clean, creds)
            if note_ack:
                try:
                    notify_telegram.send_message(creds, note_ack)
                except Exception:
                    pass
            logged += 1
            continue

        # Not a command/plan → handle activity logging and assemble the reply.
        strong_workout = (bool(dist_km) or bool(dur_min)
                          or atype in ("run", "bike", "walk", "swim", "yoga", "hiit")
                          or any(w in low for w in WORKOUT_SIGNAL))
        # For pain/status messages, only log an activity on a strong signal (so
        # "my lower back is tight" doesn't get mis-logged as a strength session).
        has_workout = strong_workout if (is_pain or is_status) else (
            (atype != "other") or dist_km or dur_min or any(w in low for w in WORKOUT_SIGNAL))

        reply_bits = []
        if note_ack:
            reply_bits.append(note_ack)

        if has_workout:
            dup = (not override) and any(
                e.get("date") == msg_day and e.get("type") == atype
                for e in activity_log.read_all())
            if dup:
                reply_bits.append(f"(a '{atype}' is already logged today — not "
                    "double-counting; text 'also: …' to force a separate one.)")
            else:
                entry = {"source": "telegram", "type": atype, "date": msg_day,
                         "title": clean[:80], "detail": clean[:300]}
                if dist_km:
                    entry["distance_km"] = dist_km
                if dur_min:
                    entry["duration_min"] = dur_min
                activity_log.append_entry(entry, dedup=False)
                logged += 1
                reply_bits.append(confirm_text(atype, dist_km, dur_min, hevy_units_lb()))

        # Neither pain/status nor workout → keep as a general note (nothing lost).
        if not (is_pain or is_status) and not has_workout:
            notes.append_note(clean, kind="note", when=msg_day)
            logged += 1
            reply_bits.append("Noted ✓")

        if reply_bits:
            try:
                notify_telegram.send_message(creds, " ".join(reply_bits))
            except Exception:
                pass

    if last_update > offset:
        save_offset(last_update)
    if not quiet:
        print(f"ingest: {logged} message(s) logged.")
    return logged


def hevy_units_lb():
    try:
        import hevy_sync
        return hevy_sync.detect_units() == "lb"
    except Exception:
        return False


def main():
    p = argparse.ArgumentParser(description="Ingest Telegram messages into the activity log.")
    p.add_argument("--poll", action="store_true", help="process new messages once")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()
    poll(quiet=args.quiet)


if __name__ == "__main__":
    main()
