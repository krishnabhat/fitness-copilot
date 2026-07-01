#!/bin/zsh
# Monthly progress report for the fitness-copilot skill. Runs early each month:
# headless Claude computes the stats, writes a coach's narrative + next-month focus,
# reminds about due health metrics (VO2max/DEXA/etc.), and Telegrams the report.
# Scheduled by ~/Library/LaunchAgents/com.fitness-copilot.monthly.plist

export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
SKILL_DIR="$HOME/.claude/skills/fitness-copilot"
LOG="$SKILL_DIR/profile/monthly.log"
WORKDIR="$HOME/claude/fitness-copilot"
[ -d "$WORKDIR" ] || WORKDIR="$HOME"

# Once-per-month guard (failsafe times + wake catch-up shouldn't resend).
MONTHLY_STATE="$SKILL_DIR/profile/.monthly_state"
THIS_MONTH="$(date +%Y-%m)"
if [ -f "$MONTHLY_STATE" ] && [ "$(cat "$MONTHLY_STATE" 2>/dev/null)" = "$THIS_MONTH" ]; then
  echo "$(date): monthly report already sent for $THIS_MONTH — skipping." >> "$LOG"
  exit 0
fi

# Atomic lock so overlapping failsafe runs don't both send (reclaim if stale >2h).
LOCKDIR="$SKILL_DIR/profile/.monthly.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  _age=$(( $(date +%s) - $(stat -f %m "$LOCKDIR" 2>/dev/null || echo 0) ))
  if [ "$_age" -gt 7200 ]; then rmdir "$LOCKDIR" 2>/dev/null; mkdir "$LOCKDIR" 2>/dev/null || exit 0
  else echo "$(date): monthly already running; exiting." >> "$LOG"; exit 0; fi
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

read -r -d '' PROMPT <<'EOF'
Use the fitness-copilot skill to produce my MONTHLY progress report. First run
`python3 scripts/progress_report.py --days 30` to compute my stats (strength PRs and
est-1RM trends, total volume, training frequency/adherence, cardio distance & best
pace, sets per muscle) and review which health metrics are due. Then write a concise,
motivating coach's summary: 2–4 wins, anything sliding, and the focus for next month
tied to my goals and current mesocycle phase. Deliver it by running
`python3 scripts/progress_report.py --days 30 --send --note "<your summary>"`.
Also clearly remind me of any metrics due for re-measurement (VO2max, DEXA body comp,
resting HR, blood pressure, bloodwork, 1-mile time) and briefly suggest how to capture
each. Non-interactive: do not ask questions; proceed with sensible assumptions.
Finish with a one-paragraph chat summary.
EOF

cd "$WORKDIR" 2>/dev/null
{
  echo "================ monthly report: $(date) ================"
  claude -p "$PROMPT" --permission-mode bypassPermissions --add-dir "$SKILL_DIR"

  # Fallback: if no report was produced today, send a stats-only one so a report always goes out.
  TODAY="$(date +%Y-%m-%d)"
  if [ ! -f "$SKILL_DIR/reports/$TODAY-progress.html" ] && [ -f "$SKILL_DIR/profile/.telegram" ]; then
    echo "--- no report from Claude run; sending stats-only fallback ---"
    python3 "$SKILL_DIR/scripts/progress_report.py" --days 30 --send || echo "(fallback failed)"
  fi

  # Mark the month done ONLY if a report was actually produced today — otherwise a
  # later failsafe run (or the Day-2 catch-up) retries instead of silently skipping.
  if [ -f "$SKILL_DIR/reports/$TODAY-progress.html" ]; then
    echo "$THIS_MONTH" > "$MONTHLY_STATE"
    chmod 600 "$MONTHLY_STATE" 2>/dev/null
  else
    echo "No report produced — not marking month done; will retry on the next fire."
  fi
  echo "================ done: $(date) ================"
  echo
} >> "$LOG" 2>&1
