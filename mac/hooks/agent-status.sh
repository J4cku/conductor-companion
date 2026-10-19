#!/usr/bin/env bash
# Session state for the menu bar plugin ~/.claude/agent-status/plugins/agent-status.py.
# One file per session in ~/.claude/agent-status/sessions/: state, since, workspace name, Conductor workspace id,
# what it is doing, joined by \x1f. Every event rewrites it, so its mtime is the session's heartbeat.
# Conductor's own database owns working/idle/unread; this adds what only hooks see: a session blocked on you,
# and the tool it is running. Also counts today's finished turns (gold) and tool calls (lumber).
# Events from inside a subagent only beat the heart and chop lumber: the main thread owns the state.

exec >/dev/null 2>&1 # UserPromptSubmit stdout would land in the model's context
DIR="$HOME/.claude/agent-status"
mkdir -p "$DIR/sessions" "$DIR/stats"

DOING='def clip($n): gsub("[\\s\u001f]+"; " ") | ltrimstr(" ") | if length > $n then .[:$n - 1] + "…" else . end;
.hook_event_name as $event
| (.tool_name // "") as $tool
| (.tool_input // {}) as $in
| if $event == "UserPromptSubmit" then .prompt // "" | if startswith("<") then "" else . end
  elif $event == "Notification" then .message // ""
  elif $event == "PreToolUse" or $event == "PermissionRequest" then
    (if $tool | test("AskUserQuestion") then $in.questions[0].question // "Has a question"
     elif $tool == "ExitPlanMode" then "Plan ready for review"
     elif $tool == "Bash" then "Bash: \($in.description // $in.command)"
     elif $tool | test("^(Read|Edit|Write|NotebookEdit)$") then "\($tool): \($in.file_path // $in.notebook_path // "" | split("/") | last)"
     elif $tool == "Grep" or $tool == "Glob" then "\($tool): \($in.pattern)"
     elif $tool == "Agent" or $tool == "Task" then "Agent: \($in.description)"
     elif $tool == "WebFetch" then "WebFetch: \($in.url // "" | sub("^https?://"; "") | split("/")[0])"
     elif $tool == "WebSearch" then "WebSearch: \($in.query)"
     elif $tool == "Skill" then "Skill: \($in.skill)"
     elif $tool | startswith("mcp__") then $tool | ltrimstr("mcp__") | sub("__"; ": ")
     else $tool end)
  else "" end
| clip(80)'

input=$(cat)
IFS=$'\x1f' read -r event session agent tool cwd now today doing < <(jq -r "[.hook_event_name, .session_id, .agent_id // \"\", .tool_name // \"\", .cwd // \"\", (now | floor), (now | strflocaltime(\"%Y-%m-%d\")), ($DOING)] | map(tostring) | join(\"\u001f\")" <<<"$input")
[ -n "$session" ] || exit 0
# Conductor's helper calls (chat titles) run in a workspace folder without a workspace id; they are not chats.
[ -z "${CONDUCTOR_WORKSPACE_ID:-}" ] && case "$cwd" in "$HOME"/conductor/*) exit 0 ;; esac
file="$DIR/sessions/$session"

[ "$event" = PostToolUse ] && printf . >>"$DIR/stats/$today.lumber"
if [ -n "$agent" ]; then
  [ -f "$file" ] && touch "$file"
  exit 0
fi

case "$event" in
  Stop)
    printf . >>"$DIR/stats/$today.gold"
    state=done
    ;;
  StopFailure) state=failed ;;
  PermissionRequest | Notification) state=waiting ;;
  PreToolUse) [[ $tool =~ AskUserQuestion|ExitPlanMode ]] && state=waiting || state=working ;;
  *) state=working ;;
esac

since=$now
if IFS=$'\x1f' read -r prev prev_since _ _ prev_doing <"$file"; then
  [ "$prev" = "$state" ] && since=$prev_since
  [ -n "$doing" ] || doing=$prev_doing
fi
printf '%s\x1f%s\x1f%s\x1f%s\x1f%s\n' "$state" "$since" "${CONDUCTOR_WORKSPACE_NAME:-${cwd##*/}}" "${CONDUCTOR_WORKSPACE_ID:-}" "$doing" >"$file.$$" && mv "$file.$$" "$file"
