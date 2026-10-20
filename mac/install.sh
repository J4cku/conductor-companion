#!/bin/bash
# Installs the Mac side of Conductor Companion from this checkout: links the menu bar plugin and the hooks into
# ~/.claude and registers the hooks in ~/.claude/settings.json.
# Re-runnable: links are replaced, hooks are only added once. Also removes the retired phone server and phone relay hook.
set -euo pipefail
REPO=$(cd "$(dirname "$0")" && pwd)
ROOT="$HOME/.claude/agent-status"
HOOKS="$HOME/.claude/hooks"
SETTINGS="$HOME/.claude/settings.json"

for tool in jq /usr/bin/python3; do command -v "$tool" >/dev/null || { echo "missing: $tool"; exit 1; }; done
[ -d /Applications/SwiftBar.app ] || echo "note: SwiftBar is not installed (brew install --cask swiftbar); the menu bar item needs it"

mkdir -p "$ROOT/plugins" "$ROOT/sessions" "$ROOT/stats" "$HOOKS"
ln -sfn "$REPO/menubar/agent-status.py" "$ROOT/plugins/agent-status.py"
for hook in agent-status.sh wc3.sh; do ln -sfn "$REPO/hooks/$hook" "$HOOKS/$hook"; done

# Register each hook once (and drop the retired phone relay). Notification only for the prompts that mean "the agent needs you".
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.bak-conductor-companion"
jq --arg status "$HOOKS/agent-status.sh" --arg relay "$HOOKS/phone-relay.sh" '
  def add($event; $entry): .hooks[$event] = ((.hooks[$event] // []) | if any(tostring | contains($entry.hooks[0].command)) then . else . + [$entry] end);
  .hooks.PreToolUse = ((.hooks.PreToolUse // []) | map(select(tostring | contains($relay) | not)))
  | reduce ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest", "PreCompact", "Stop", "StopFailure") as $e
    (.; add($e; {hooks: [{type: "command", command: $status, timeout: 5}]}))
  | add("Notification"; {matcher: "permission_prompt|elicitation_dialog|elicitation_url_dialog|agent_needs_input", hooks: [{type: "command", command: $status, timeout: 5}]})
' "$SETTINGS.bak-conductor-companion" > "$SETTINGS"

# Retired: the phone server and its relay hook (Conductor now reaches local workspaces from its own phone app).
for agent in com.conductor-companion.phone com.jacek.agent-status-phone; do
  launchctl bootout "gui/$(id -u)/$agent" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$agent.plist"
done
rm -f "$HOOKS/phone-relay.sh" "$HOOKS/phone-relay.py" "$ROOT/phone/server.py" "$ROOT/phone/app.html"

# SwiftBar reads plugins from our folder (unless you already pointed it elsewhere).
defaults read com.ameba.SwiftBar PluginDirectory >/dev/null 2>&1 || defaults write com.ameba.SwiftBar PluginDirectory -string "$ROOT/plugins"
open -g "swiftbar://refreshplugin?name=agent-status" 2>/dev/null || true
echo "installed from $REPO"
[ -d "$ROOT/phone" ] && echo "note: $ROOT/phone still holds the old phone server's data (paired devices, logs, push key); delete it when you no longer need it" || true
