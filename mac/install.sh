#!/bin/bash
# Installs the Mac side of Conductor Companion from this checkout: links the menu bar plugin, the phone server and
# the hooks into ~/.claude, registers the hooks in ~/.claude/settings.json, and starts the phone server at login.
# Re-runnable: links are replaced, hooks are only added once.
set -euo pipefail
REPO=$(cd "$(dirname "$0")" && pwd)
ROOT="$HOME/.claude/agent-status"
HOOKS="$HOME/.claude/hooks"
SETTINGS="$HOME/.claude/settings.json"
AGENT="com.conductor-companion.phone"
LEGACY="com.jacek.agent-status-phone"

for tool in jq /usr/bin/python3; do command -v "$tool" >/dev/null || { echo "missing: $tool"; exit 1; }; done
[ -d /Applications/SwiftBar.app ] || echo "note: SwiftBar is not installed (brew install --cask swiftbar); the menu bar item needs it"
command -v qrencode >/dev/null || echo "note: qrencode is not installed (brew install qrencode); the pairing QR code needs it"

mkdir -p "$ROOT/plugins" "$ROOT/sessions" "$ROOT/stats" "$ROOT/phone/waiting" "$ROOT/phone/inbox" "$ROOT/phone/sent" "$HOOKS"
ln -sfn "$REPO/menubar/agent-status.py" "$ROOT/plugins/agent-status.py"
ln -sfn "$REPO/server/server.py" "$ROOT/phone/server.py"
ln -sfn "$REPO/server/app.html" "$ROOT/phone/app.html"
for hook in agent-status.sh phone-relay.sh phone-relay.py wc3.sh; do ln -sfn "$REPO/hooks/$hook" "$HOOKS/$hook"; done

# Register each hook once. Notification only for the prompts that mean "the agent needs you".
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.bak-conductor-companion"
jq --arg status "$HOOKS/agent-status.sh" --arg relay "$HOOKS/phone-relay.sh" '
  def add($event; $entry): .hooks[$event] = ((.hooks[$event] // []) | if any(tostring | contains($entry.hooks[0].command)) then . else . + [$entry] end);
  reduce ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest", "PreCompact", "Stop", "StopFailure") as $e
    (.; add($e; {hooks: [{type: "command", command: $status, timeout: 5}]}))
  | add("Notification"; {matcher: "permission_prompt|elicitation_dialog|elicitation_url_dialog|agent_needs_input", hooks: [{type: "command", command: $status, timeout: 5}]})
  | add("PreToolUse"; {matcher: ".*AskUserQuestion", hooks: [{type: "command", command: $relay, timeout: 3600}]})
' "$SETTINGS.bak-conductor-companion" > "$SETTINGS"

# The phone server, kept alive by launchd.
PLIST="$HOME/Library/LaunchAgents/$AGENT.plist"
sed "s#__HOME__#$HOME#g" "$REPO/launchd/conductor-companion.plist.template" > "$PLIST"
launchctl bootout "gui/$(id -u)/$LEGACY" 2>/dev/null && rm -f "$HOME/Library/LaunchAgents/$LEGACY.plist" || true
launchctl bootout "gui/$(id -u)/$AGENT" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

# SwiftBar reads plugins from our folder (unless you already pointed it elsewhere).
defaults read com.ameba.SwiftBar PluginDirectory >/dev/null 2>&1 || defaults write com.ameba.SwiftBar PluginDirectory -string "$ROOT/plugins"
open -g "swiftbar://refreshplugin?name=agent-status" 2>/dev/null || true
echo "installed from $REPO"
