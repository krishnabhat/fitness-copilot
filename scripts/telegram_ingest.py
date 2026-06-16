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
import os
import re
import sys
import urllib.parse
import urllib.request
import json
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notify_telegram   # noqa: E402  (load_creds, send_message)
import activity_log      # noqa: E402
import notes             # noqa: E402

PAIN_WORDS = ["pain", "hurt", "sore", "soreness", "ache", "aching", "tweak",
              "stiff", "strain", "sprain", "injured", "injury", "tight",
              "pulled", "spasm", "cramp", "throb"]
STATUS_WORDS = ["tired", "exhausted", "fatigued", "didn't sleep", "didnt sleep",
                "stressed", "sick", "ill", "flu", "run down", "rundown",
                "low energy", "drained", "burnt out", "burned out", "wiped"]
RED_FLAG_WORDS = ["sharp", "radiating", "shooting", "numb", "tingl", "chest pain",
                  "can't move", "cant move", "severe", "dizzy", "faint",
                  "short of breath", "shortness of breath"]
BODY_PARTS = ["lower back", "upper back", "back", "neck", "shoulder", "knee",
              "hip", "ankle", "wrist", "elbow", "hamstring", "quad", "calf",
              "groin", "spine", "disc", "achilles", "foot", "leg", "thigh"]
WORKOUT_SIGNAL = ["workout", "session", "trained", "exercised", "gym", "wod"]

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
        atype, dist_km, dur_min = parse_activity(clean)
        is_pain = any(w in low for w in PAIN_WORDS)
        is_status = any(w in low for w in STATUS_WORDS)
        red_flag = any(w in low for w in RED_FLAG_WORDS)
        # A "strong" workout signal can't be a false positive from ambiguous words
        # like "lower"/"leg" (which appear in "lower back"/"my leg hurts").
        strong_workout = (bool(dist_km) or bool(dur_min)
                          or atype in ("run", "bike", "walk", "swim", "yoga", "hiit")
                          or any(w in low for w in WORKOUT_SIGNAL))
        # For pain/status messages, only log an activity on a strong signal (so
        # "my lower back is tight" doesn't get mis-logged as a strength session).
        has_workout = strong_workout if (is_pain or is_status) else (
            (atype != "other") or dist_km or dur_min or any(w in low for w in WORKOUT_SIGNAL))
        part = next((b for b in BODY_PARTS if b in low), None)
        reply_bits = []

        # 1) Pain / status note → constraints store (NEVER counts as a workout).
        if is_pain or is_status:
            notes.append_note(clean, kind="pain" if is_pain else "status", red_flag=red_flag)
            logged += 1
            if red_flag:
                reply_bits.append("⚠️ Noted — that can be serious. If it's sharp, "
                    "radiating, numb, or comes with chest pain/dizziness, please STOP "
                    "and see a clinician. I'll keep training off that area until you're cleared.")
            elif is_pain:
                reply_bits.append(f"Got it — noted your {part or 'pain'}. I'll program "
                    "around it next session (avoid loading that area). Tell me if it "
                    "turns sharp or radiates.")
            else:
                reply_bits.append("Noted — I'll factor that into your next plan "
                    "(lighter if you're run down).")

        # 2) Activity (with cross-source duplicate guard).
        if has_workout:
            today = date.today().isoformat()
            dup = (not override) and any(
                e.get("date") == today and e.get("type") == atype
                for e in activity_log.read_all())
            if dup:
                reply_bits.append(f"(a '{atype}' is already logged today — not "
                    "double-counting; text 'also: …' to force a separate one.)")
            else:
                entry = {"source": "telegram", "type": atype,
                         "title": clean[:80], "detail": clean[:300]}
                if dist_km:
                    entry["distance_km"] = dist_km
                if dur_min:
                    entry["duration_min"] = dur_min
                activity_log.append_entry(entry, dedup=False)
                logged += 1
                reply_bits.append(confirm_text(atype, dist_km, dur_min, hevy_units_lb()))

        # 3) Neither → keep as a general note (nothing lost, no workout inflation).
        if not (is_pain or is_status) and not has_workout:
            notes.append_note(clean, kind="note")
            logged += 1
            reply_bits.append("Noted ✓")

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
