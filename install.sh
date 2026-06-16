#!/bin/zsh
# install.sh — set up the fitness-copilot scheduled jobs for the CURRENT user.
# Generic: no usernames or absolute paths are baked into the repo — this script
# generates the launchd agents from your own $HOME and python3 at install time.
#
# Prereqs: this skill folder must live at ~/.claude/skills/fitness-copilot
#          (the scripts reference that path), and `claude` must be on your PATH.
#
# Usage:  ./install.sh          # create + load the 4 scheduled jobs
#         ./uninstall.sh        # remove them

set -e
SKILL_DIR="$HOME/.claude/skills/fitness-copilot"
LA="$HOME/Library/LaunchAgents"
PY="$(command -v python3 || echo /usr/bin/python3)"
ZSH="$(command -v zsh || echo /bin/zsh)"
UID_=$(id -u)
mkdir -p "$LA"

if [ ! -d "$SKILL_DIR" ]; then
  echo "ERROR: expected the skill at $SKILL_DIR"
  echo "Move/clone this folder there first, then re-run ./install.sh"
  exit 1
fi

# helper: write a plist that runs a program with a set of <StartCalendarInterval>
# dicts (passed as a single XML blob) or a StartInterval.
make_plist () {
  local label="$1" ; local prog_xml="$2" ; local sched_xml="$3" ; local logname="$4"
  cat > "$LA/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array>$prog_xml</array>
  $sched_xml
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$SKILL_DIR/profile/$logname</string>
  <key>StandardErrorPath</key><string>$SKILL_DIR/profile/$logname</string>
</dict></plist>
PLIST
  launchctl bootout gui/$UID_/$label 2>/dev/null || true
  launchctl bootstrap gui/$UID_ "$LA/$label.plist"
  echo "  ✓ $label"
}

cal () { echo "<dict>$@</dict>"; }   # convenience for a calendar entry

echo "Installing fitness-copilot scheduled jobs for $(whoami)…"

# Nightly auto-planner — multiple failsafe times.
make_plist "com.fitness-copilot.autoplan" \
  "<string>$ZSH</string><string>$SKILL_DIR/scripts/nightly_autoplan.sh</string>" \
  "<key>StartCalendarInterval</key><array>
     $(cal '<key>Hour</key><integer>21</integer><key>Minute</key><integer>0</integer>')
     $(cal '<key>Hour</key><integer>22</integer><key>Minute</key><integer>30</integer>')
     $(cal '<key>Hour</key><integer>23</integer><key>Minute</key><integer>45</integer>')
     $(cal '<key>Hour</key><integer>6</integer><key>Minute</key><integer>30</integer>')
     $(cal '<key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer>')
   </array>" \
  "autoplan.launchd.log"

# Weekly preview — Fridays (Weekday 5) + Saturday catch-up.
make_plist "com.fitness-copilot.weekly" \
  "<string>$ZSH</string><string>$SKILL_DIR/scripts/weekly_plan.sh</string>" \
  "<key>StartCalendarInterval</key><array>
     $(cal '<key>Weekday</key><integer>5</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer>')
     $(cal '<key>Weekday</key><integer>5</integer><key>Hour</key><integer>19</integer><key>Minute</key><integer>30</integer>')
     $(cal '<key>Weekday</key><integer>5</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>0</integer>')
     $(cal '<key>Weekday</key><integer>6</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer>')
   </array>" \
  "weekly.launchd.log"

# Monthly report — 1st (+ Day 2 catch-up).
make_plist "com.fitness-copilot.monthly" \
  "<string>$ZSH</string><string>$SKILL_DIR/scripts/monthly_report.sh</string>" \
  "<key>StartCalendarInterval</key><array>
     $(cal '<key>Day</key><integer>1</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer>')
     $(cal '<key>Day</key><integer>1</integer><key>Hour</key><integer>20</integer><key>Minute</key><integer>0</integer>')
     $(cal '<key>Day</key><integer>2</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer>')
   </array>" \
  "monthly.launchd.log"

# Telegram ingest — every 15 minutes.
make_plist "com.fitness-copilot.ingest" \
  "<string>$PY</string><string>$SKILL_DIR/scripts/telegram_ingest.py</string><string>--poll</string><string>--quiet</string>" \
  "<key>StartInterval</key><integer>900</integer>" \
  "ingest.log"

echo "Done. 4 jobs scheduled. They run only when you've set up the relevant keys"
echo "(HEVY, Telegram, Oura). Manage with: launchctl list | grep fitness-copilot"
