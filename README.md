# 🏋️ Fitness Copilot

**Your personal trainer, sports nutritionist, and sports-medicine physician — in one AI coach that lives in your terminal and your phone.**

Fitness Copilot is a [Claude Code](https://claude.com/claude-code) skill that designs personalized, *progressive*, safety-first training — and actually keeps up with you over time. It reads what you really did (from HEVY, your wearables, or a quick text), reasons about your recovery, injuries, and labs, then programs your next session, writes it straight into your workout app, and delivers it to your phone.

It's not a static plan. It's a coach that adapts every day.

---

## What it does

- **Three experts in one** — elite S&C coach + sports nutritionist + sports-medicine physician, on every recommendation.
- **Truly personalized** — programs around *your* goals, equipment, schedule, injuries, labs, and the time you have today.
- **Progressive & periodized** — double-progression per lift, mesocycles (build → deload), and autoregulation based on your recovery. It gets you better over months, not just busier.
- **Recovery-aware** — pulls daily HRV / resting HR / readiness (Oura) and automatically lightens the day when you're run down.
- **Equipment-smart** — full gym, garage, minimal home, or hotel-room bodyweight: same goal, right tools.
- **Beautiful workouts** — each session is a clean HTML page with sets/reps/rest, coaching cues, fuel, and a how-to video link per exercise.
- **Writes to your app** — pushes the session into HEVY as a routine so you just check off sets.
- **Logs everything, from anywhere** — HEVY, Apple Health, Oura, or just text the bot "ran 5k in 28 min." One unified log, cross-source de-duplicated.
- **On autopilot** — plans tomorrow's session nightly, a week-ahead preview every Friday, and a monthly progress report — all delivered to Telegram.
- **Tracks the metrics that matter** — weight, BMI, HRV, resting HR, VO2max, body comp — and nudges you when it's time to re-measure (DEXA, bloodwork, etc.).

## Safety first

Fitness Copilot is coaching decision-support, **not** medical advice and **not** a substitute for a licensed clinician. It has hard-coded red-flag triage (chest pain, radiating pain, neurological symptoms, etc.), pain rules, and conservative defaults, and it routes anything medical to a professional. Always get clearance before vigorous exercise if you have relevant conditions.

## Requirements

- **Claude Code** (the CLI) — this is a Claude Code skill.
- **macOS** for the automated scheduling (launchd). The skill itself works cross-platform; only the auto-scheduling is macOS-specific.
- **Python 3** (standard library only — no `pip install`).
- Optional integrations: **Hevy Pro** (workout read/write), **Oura** (recovery metrics), **Telegram bot** (delivery + text logging), **Apple Health** (history import).

## Install

```bash
# 1. Put the skill where Claude Code finds it
git clone <your-repo-url> ~/.claude/skills/fitness-copilot

# 2. (Optional) set up the automated nightly/weekly/monthly jobs + Telegram ingest
cd ~/.claude/skills/fitness-copilot && ./install.sh
```

Then open Claude Code and say **"set up my fitness profile"** or **"plan my workout today."** On first run it interviews you (goals, equipment, schedule, injuries, etc.) and saves a private, gitignored profile.

### Connect your data (all optional)
- **HEVY** (`profile/.hevy_key`): hevy.com → Settings → Developer/API (needs Hevy Pro)
- **Oura** (`profile/.oura_key`): cloud.ouraring.com → Personal Access Tokens → `python3 scripts/oura_sync.py --setup "<token>"`
- **Telegram**: create a bot via @BotFather → `python3 scripts/notify_telegram.py --setup "<token>"`
- **Apple Health**: Health app → Export All Health Data → `python3 scripts/applehealth_import.py <export.zip>`

Uninstall the scheduled jobs anytime with `./uninstall.sh` (your data is kept).

## How it works

```
profile.md ─┐
HEVY  ──────┤
Oura  ──────┼──▶  activity_log + health_metrics  ──▶  Claude (coach reasoning)  ──▶  HTML session
Telegram ───┤        (unified, de-duplicated)              + progression/periodization      + HEVY routine
Apple Health┘                                              + recovery autoregulation         + Telegram delivery
```

Nightly, it refreshes your data from every source, checks whether you've completed your queued workout (so it never piles up plans), reads your recovery, and programs the next session — pushed to HEVY and your phone.

## Privacy

All personal data — your profile, API keys, health metrics, activity log, generated workouts — stays **local** and is gitignored. Nothing is uploaded anywhere except the calls *you* configure (HEVY/Oura/Telegram APIs). API keys are never printed.

## Repository layout

```
fitness-copilot/
├── SKILL.md                  # the coach: persona, safety, operating logic
├── install.sh / uninstall.sh # set up / remove the scheduled jobs (per-user)
├── reference/                # training, nutrition, sports-medicine, equipment knowledge
├── scripts/                  # HEVY, Oura, Apple Health, Telegram, reports, logging
└── profile/
    └── profile.template.md   # copied to your private profile.md on first run
```

## Disclaimer

Provided as-is, for educational and personal-coaching purposes. Not medical advice. The authors are not liable for injury or health outcomes. Use at your own risk and consult qualified professionals.
