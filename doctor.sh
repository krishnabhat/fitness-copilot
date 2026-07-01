#!/bin/zsh
# doctor.sh — verify Fitness Copilot is set up correctly on this machine, so new
# users can self-diagnose instead of hitting silent failures. Read-only; changes nothing.

SKILL_DIR="$HOME/.claude/skills/fitness-copilot"
ok()   { print "  \033[32m✓\033[0m $1" }
warn() { print "  \033[33m⚠\033[0m $1" }
bad()  { print "  \033[31m✗\033[0m $1" }

print "\n🩺 Fitness Copilot — setup check\n"

print "Core:"
if [ -d "$SKILL_DIR" ]; then ok "skill installed at ~/.claude/skills/fitness-copilot"
else bad "skill is NOT at $SKILL_DIR — clone it there (the scripts expect that path)"; fi
if command -v python3 >/dev/null 2>&1; then ok "python3 found ($(command -v python3))"
else bad "python3 not found — install Python 3"; fi
if command -v claude >/dev/null 2>&1; then ok "claude CLI found ($(command -v claude))"
else warn "claude CLI not on PATH — needed for the auto-planner (manual use still works)"; fi
if [ "$(uname)" = "Darwin" ]; then ok "macOS — automated scheduling supported"
else warn "not macOS — the coach works, but auto-scheduling (launchd) is macOS-only"; fi

print "\nYour profile:"
if [ -f "$SKILL_DIR/profile/profile.md" ]; then ok "profile.md exists"
else warn "no profile yet — open Claude Code and say \"set up my fitness profile\""; fi

print "\nIntegrations (all optional):"
[ -f "$SKILL_DIR/profile/.hevy_key" ]  && ok "HEVY connected"     || warn "HEVY not connected — see README (needs Hevy Pro)"
[ -f "$SKILL_DIR/profile/.oura_key" ]  && ok "Oura connected"     || warn "Oura not connected — oura_sync.py --setup"
[ -f "$SKILL_DIR/profile/.telegram" ]  && ok "Telegram connected" || warn "Telegram not connected — notify_telegram.py --setup"

if [ "$(uname)" = "Darwin" ]; then
  print "\nAutomation:"
  n=$(launchctl list 2>/dev/null | grep -c fitness-copilot)
  if [ "${n:-0}" -gt 0 ]; then ok "$n scheduled job(s) loaded"
  else warn "no scheduled jobs — run ./install.sh to enable nightly/weekly/monthly autopilot"; fi
fi

print "\nNext step: in Claude Code, say \"plan my workout\" — it'll onboard you if you're new.\n"
