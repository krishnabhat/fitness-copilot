#!/bin/zsh
# Nightly auto-planner for the fitness-copilot skill.
# Runs headless Claude Code at ~9 PM to plan tomorrow's workout, render the HTML,
# and push it to HEVY as a routine — so in the morning you just open HEVY and train.
# Scheduled by ~/Library/LaunchAgents/com.fitness-copilot.autoplan.plist
# Logs to profile/autoplan.log. To disable: launchctl bootout gui/$(id -u)/com.fitness-copilot.autoplan

# launchd starts with a minimal PATH — set the tools we need explicitly.
export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

SKILL_DIR="$HOME/.claude/skills/fitness-copilot"
LOG="$SKILL_DIR/profile/autoplan.log"
WORKDIR="$HOME/claude/fitness-copilot"
[ -d "$WORKDIR" ] || WORKDIR="$HOME"

# Atomic lock so overlapping failsafe runs (or a slow prior run) don't double-plan.
# mkdir is atomic; reclaim a stale lock older than 2h (a crashed prior run).
LOCKDIR="$SKILL_DIR/profile/.autoplan.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  _age=$(( $(date +%s) - $(stat -f %m "$LOCKDIR" 2>/dev/null || echo 0) ))
  if [ "$_age" -gt 7200 ]; then rmdir "$LOCKDIR" 2>/dev/null; mkdir "$LOCKDIR" 2>/dev/null || exit 0
  else echo "$(date): autoplan already running; exiting." >> "$LOG"; exit 0; fi
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

read -r -d '' PROMPT <<'EOF'
Use the fitness-copilot skill to plan my NEXT training session.
Read my profile and recent HEVY history, then pick the best next session given my
recent training, my goals, the time and equipment I have available, and any
injuries or health constraints noted in my profile. Balance the
weekly split against what I trained most recently.
Apply PROGRESSIVE OVERLOAD: check `python3 scripts/hevy_sync.py --progress` and
`python3 scripts/mesocycle.py --status`, then set each lift's targets to beat my
last comparable session via double-progression, programmed to the current
mesocycle phase (build weeks ramp volume/intensity; a deload week backs off ~40–50%).
Render the HTML session page,
then create it as a routine in HEVY so it's ready for my next workout.
Check my latest readiness via `python3 scripts/health_metrics.py --summary` (Oura
HRV, resting HR, readiness score): if HRV is trending down / resting HR up / readiness
low, scale today down (partial deload — less volume, lower intensity); otherwise
program normally. ALSO run `python3 scripts/notes.py --recent 7` and respect any pain/
injury/status I reported: program around a sore/injured area (avoid loading it, swap to
pain-free movements), go lighter if I'm run down, and if anything is flagged red
(sharp/radiating pain, numbness, chest pain) do NOT load that area — note it and advise care. This is an automated, non-interactive run: do NOT ask me any
questions. If anything is ambiguous, proceed with sensible, safe assumptions and
note them. Finish with a one-paragraph summary.
EOF

cd "$WORKDIR" 2>/dev/null
{
  echo "================ nightly autoplan: $(date) ================"

  # Refresh the local activity log + health metrics from all sources before planning.
  python3 "$SKILL_DIR/scripts/telegram_ingest.py" --poll --quiet 2>/dev/null || true
  python3 "$SKILL_DIR/scripts/activity_log.py" --sync-hevy 2>/dev/null || true
  [ -f "$SKILL_DIR/profile/.oura_key" ] && \
    python3 "$SKILL_DIR/scripts/oura_sync.py" --days 3 2>/dev/null || true
  python3 "$SKILL_DIR/scripts/activity_log.py" --dedupe 2>/dev/null || true

  # Gate: only plan a new workout if the previously planned one was completed
  # (i.e. a workout was logged in HEVY since we last planned). Otherwise skip.
  if python3 "$SKILL_DIR/scripts/autoplan_gate.py"; then
    claude -p "$PROMPT" --permission-mode bypassPermissions --add-dir "$SKILL_DIR"
    _RC=$?
    # Only record the plan + deliver if the planning run actually succeeded. Recording
    # on failure would make the gate think we planned and skip the retry next run.
    if [ "$_RC" -eq 0 ]; then
      python3 "$SKILL_DIR/scripts/autoplan_gate.py" --record
      if [ -f "$SKILL_DIR/profile/.telegram" ]; then
        echo "--- sending to Telegram ---"
        python3 "$SKILL_DIR/scripts/notify_telegram.py" --latest \
          --caption "Tomorrow's workout is ready 💪 Open in HEVY or tap the file." \
          || echo "(telegram send failed)"
      fi
    else
      echo "Planning run failed (claude exit $_RC) — NOT recording; will retry next run."
    fi
  else
    echo "Skipping plan: previous workout not completed yet (or HEVY unavailable)."
    # Still nudge: re-send the pending workout HTML(s) (once/day guard inside).
    python3 "$SKILL_DIR/scripts/autoplan_gate.py" --remind 2>/dev/null || true
  fi
  echo "================ done: $(date) ================"
  echo
} >> "$LOG" 2>&1
