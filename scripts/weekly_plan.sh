#!/bin/zsh
# Weekly preview for the fitness-copilot skill. Runs Friday evening: headless
# Claude builds a week-at-a-glance roadmap for the coming week and Telegrams it.
# This is informational only — it does NOT push routines to HEVY (the nightly
# planner builds each day's detailed session + routine the night before).
# Scheduled by ~/Library/LaunchAgents/com.fitness-copilot.weekly.plist

export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
SKILL_DIR="$HOME/.claude/skills/fitness-copilot"
LOG="$SKILL_DIR/profile/weekly.log"
WORKDIR="$HOME/claude/fitness-copilot"
[ -d "$WORKDIR" ] || WORKDIR="$HOME"

read -r -d '' PROMPT <<'EOF'
Use the fitness-copilot skill to create a WEEKLY training plan for the upcoming
week (Monday through Sunday). Read my profile and recent HEVY history, then lay
out a balanced split for my goals: pick a sensible focus for each
day (strength/push/pull/lower, Zone-2 cardio, optional HIIT,
mobility/yoga, and rest days). For HIIT days, VARY the modality — it does not have
to be running or biking; a dumbbell/bodyweight metcon (EMOM, complex, Tabata, AMRAP)
is great too — keep it safe for any injuries/health flags in my profile. Respect the time and equipment I have,
and any health constraints in my profile. Balance weekly volume
across movement patterns and don't stack hard lower-body + HIIT + long cardio on
back-to-back days. Reflect my current periodization phase
(`python3 scripts/mesocycle.py --status`) — e.g. if a deload week falls here, make it
a lighter week — and aim to progress on last week (slightly more volume/load) unless
deloading. This is a high-level ROADMAP (focus + 1-line summary per day),
NOT full per-session prescriptions — the nightly planner builds each day in detail.
Render it with scripts/build_week.py to an HTML file in the plans/ folder. Do NOT
create HEVY routines. This is an automated non-interactive run: do not ask
questions; assume sensible defaults. Finish with a one-paragraph summary.
EOF

# Once-per-week guard: with multiple failsafe fire times (and wake-from-sleep
# catch-up), make sure we only build + send one weekly plan per ISO week.
WEEKLY_STATE="$SKILL_DIR/profile/.weekly_state"
THIS_WEEK="$(date +%G-%V)"
if [ -f "$WEEKLY_STATE" ] && [ "$(cat "$WEEKLY_STATE" 2>/dev/null)" = "$THIS_WEEK" ]; then
  echo "$(date): weekly plan already sent for ISO week $THIS_WEEK — skipping." >> "$LOG"
  exit 0
fi

# Atomic lock so overlapping failsafe runs don't both send (reclaim if stale >2h).
LOCKDIR="$SKILL_DIR/profile/.weekly.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  _age=$(( $(date +%s) - $(stat -f %m "$LOCKDIR" 2>/dev/null || echo 0) ))
  if [ "$_age" -gt 7200 ]; then rmdir "$LOCKDIR" 2>/dev/null; mkdir "$LOCKDIR" 2>/dev/null || exit 0
  else echo "$(date): weekly already running; exiting." >> "$LOG"; exit 0; fi
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

cd "$WORKDIR" 2>/dev/null
{
  echo "================ weekly plan: $(date) ================"
  claude -p "$PROMPT" --permission-mode bypassPermissions --add-dir "$SKILL_DIR"
  _RC=$?

  if [ -f "$SKILL_DIR/profile/.telegram" ]; then
    LATEST=$(ls -t "$SKILL_DIR"/plans/*.html 2>/dev/null | head -1)
    if [ -n "$LATEST" ]; then
      echo "--- sending weekly plan to Telegram ---"
      python3 "$SKILL_DIR/scripts/notify_telegram.py" --html "$LATEST" \
        --caption "🗓️ Your plan for the week ahead. Each day's full session arrives the night before." \
        || echo "(telegram send failed)"
    else
      echo "(no weekly HTML found to send)"
    fi
  fi
  # Mark the ISO week done ONLY on success — else a later failsafe run retries this week.
  if [ "$_RC" -eq 0 ]; then
    echo "$THIS_WEEK" > "$WEEKLY_STATE"
    chmod 600 "$WEEKLY_STATE" 2>/dev/null
  else
    echo "Weekly run failed (claude exit $_RC) — not marking week done; will retry."
  fi
  echo "================ done: $(date) ================"
  echo
} >> "$LOG" 2>&1
