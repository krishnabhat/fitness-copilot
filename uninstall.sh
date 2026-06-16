#!/bin/zsh
# uninstall.sh — remove the fitness-copilot scheduled jobs for the current user.
# Your local data (profile, logs, keys, activity log) is left untouched.
UID_=$(id -u)
LA="$HOME/Library/LaunchAgents"
for label in autoplan weekly monthly ingest; do
  launchctl bootout gui/$UID_/com.fitness-copilot.$label 2>/dev/null && echo "  ✓ stopped com.fitness-copilot.$label" || true
  rm -f "$LA/com.fitness-copilot.$label.plist"
done
echo "Removed scheduled jobs. (Local data kept; delete ~/.claude/skills/fitness-copilot to remove the skill.)"
