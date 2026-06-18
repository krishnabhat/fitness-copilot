#!/bin/zsh
# plan_on_demand.sh — generate a workout on demand from a Telegram request and send
# it to the athlete. Invoked (detached) by telegram_ingest.py when the user texts
# something like "plan me a leg day" or "generate tomorrow's push workout".
# Takes the raw request text as $1. Runs headless Claude, renders HTML, pushes to
# HEVY, and Telegrams the result.

export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
SKILL_DIR="$HOME/.claude/skills/fitness-copilot"
LOG="$SKILL_DIR/profile/ondemand.log"
WORKDIR="$HOME/claude/fitness-copilot"
[ -d "$WORKDIR" ] || WORKDIR="$HOME"

# Note: "$REQUEST" is only ever used quoted; the shell does not re-evaluate the
# value of a variable, so request text containing $(...) or backticks is inert.
REQUEST="$1"
PROMPT="Use the fitness-copilot skill to plan a workout based on this request from me: \"$REQUEST\". Read my profile, recent training history (activity_log + HEVY), my latest readiness (health_metrics --summary) and recent pain/status notes (notes.py --recent 7). Honor the requested focus and/or day. Respect my constraints (45-minute cap unless I asked otherwise, garage dumbbells, lower-back disc, cardiac/cholesterol) and apply progressive overload. Render the HTML session page and push it to HEVY as a routine. Non-interactive: do not ask questions; proceed with safe, sensible assumptions and note them. End with a one-line summary."

cd "$WORKDIR" 2>/dev/null
{
  echo "================ on-demand plan: $(date) ================"
  echo "request: $REQUEST"
  claude -p "$PROMPT" --permission-mode bypassPermissions --add-dir "$SKILL_DIR"
  if [ -f "$SKILL_DIR/profile/.telegram" ]; then
    python3 "$SKILL_DIR/scripts/notify_telegram.py" --latest \
      --caption "💪 Here's the workout you asked for. Open in HEVY or tap to view." \
      || echo "(telegram send failed)"
  fi
  echo "================ done: $(date) ================"
} >> "$LOG" 2>&1
