#!/usr/bin/env bash
# Warcraft III flavour for Claude Code. Reads Claude Code's JSON on stdin.
#   wc3.sh hook    play a random line from ~/.claude/sounds/<theme>/<hook event>/, where <theme> is the
#                  menu bar plugin's (~/.claude/agent-status/theme), else wc3
#                  (SubagentStart: .../SubagentStart/<unit>/ for the agent's model, else .../SubagentStart/);
#                  silent while the plugin's mute file says so
#   wc3.sh agents  subagentStatusLine: each subagent row becomes the unit its model buys
#   wc3.sh status  status line segment: the session's unit, and context window as food with WC3 upkeep
# Voice packs: https://github.com/PeonPing/og-packs

SOUNDS="$HOME/.claude/sounds"
THEME="$HOME/.claude/agent-status/theme"
MUTE="$HOME/.claude/agent-status/mute" # empty: muted until turned back on; an epoch: muted until then
STATE="${TMPDIR:-/tmp}/claude-wc3"

# Haiku is a Peon, Sonnet a Grunt, Opus a Far Seer, Fable the Pandaren Brewmaster.
# Low reasoning effort is peon work whatever the model.
UNIT='def unit:
  (.model // "") as $m
  | if $m == "" then "recruit"
    elif .effort == "low" or ($m | test("haiku")) then "peon"
    elif $m | test("sonnet") then "grunt"
    elif $m | test("opus") then "farseer"
    elif $m | test("fable") then "brewmaster"
    else "grunt" end;
def title($id):
  {peon: "Peon", grunt: "Grunt", brewmaster: "Brewmaster Chen", recruit: "Recruit",
   farseer: "Far Seer \(["Thrall", "Drek\u0027Thar", "Rehgar", "Nazgrel", "Zuluhed"][$id | explode | add % 5])"}[unit];
def paint($text): "\u001b[\({peon: "33", grunt: "31", farseer: "36", brewmaster: "1;35", recruit: "2"}[unit])m\($text)\u001b[0m";'

muted() {
  local until
  until=$(cat "$MUTE" 2>/dev/null) || return 1
  [ -z "$until" ] || [ "$(date +%s)" -lt "$until" ]
}

play() {
  muted && return 0
  local theme pack dir file
  theme=$(cat "$THEME" 2>/dev/null)
  pack="$SOUNDS/${theme:-wc3}"
  [ -d "$pack" ] || pack="$SOUNDS/wc3"
  dir="$pack/$1"
  [ -d "$dir" ] || dir="${dir%/*}" # a unit without a voice of its own speaks with the event's
  file=$(find "$dir" -maxdepth 1 -type f \( -name '*.wav' -o -name '*.mp3' \) 2>/dev/null | sort -R | head -1)
  [ -n "$file" ] && afplay "$file" &
}

# Succeeds at most once per $2 seconds per key, so a fan-out of agents is one voice per unit type.
first_in() {
  local f="$STATE/$1" now
  now=$(date +%s)
  [ -f "$f" ] && [ $((now - $(<"$f"))) -lt "$2" ] && return 1
  echo "$now" >"$f"
}

# The fourth prompt inside a minute is a poke.
poked() {
  local f="$STATE/prompts-$1" now
  now=$(date +%s)
  { cat "$f" 2>/dev/null; echo "$now"; } | awk -v now="$now" 'now - $1 < 60' >"$f.new"
  mv "$f.new" "$f"
  [ "$(wc -l <"$f")" -ge 4 ] && : >"$f"
}

# SubagentStart has no model before Claude Code ~2.1.29x: take the model the Agent call asked for
# from the subagent's meta file, else the one it inherits from the session's latest turn.
# The meta file is written only after the hook returns, so callers run this in the background.
subagent_model() {
  local model transcript meta
  model=$(jq -r '.model // empty' <<<"$1")
  [ -n "$model" ] && echo "$model" && return
  transcript=$(jq -r '.transcript_path // empty' <<<"$1")
  meta="${transcript%.jsonl}/subagents/agent-$(jq -r '.agent_id' <<<"$1").meta.json"
  for _ in $(seq 50); do [ -f "$meta" ] && break; sleep 0.1; done
  model=$(jq -r '.model // empty' "$meta" 2>/dev/null)
  [ -n "$model" ] && echo "$model" && return
  tail -n 200 "$transcript" 2>/dev/null | jq -r 'select(.type == "assistant") | .message.model // empty' | tail -1
}

hook() {
  exec >/dev/null 2>&1 # SessionStart and UserPromptSubmit stdout would land in the model's context
  mkdir -p "$STATE"
  local input event session dir
  input=$(cat)
  read -r event session < <(jq -r '"\(.hook_event_name) \(.session_id)"' <<<"$input")
  case "$event" in
    SubagentStart)
      (
        dir="$event/$(jq -rn --arg model "$(subagent_model "$input")" "$UNIT"'{model: $model} | unit')"
        first_in "spawn-$session-${dir##*/}" 3 && play "$dir"
      ) &
      return 0
      ;;
    UserPromptSubmit)
      # Background agents report back as <task-notification> prompts; only what you type is a poke.
      jq -e '.prompt // "" | startswith("<") | not' <<<"$input" && poked "$session" || return 0
      dir=$event
      ;;
    *) dir=$event ;;
  esac
  play "$dir"
}

agents() {
  jq -c "$UNIT"'
    def tokens: if . >= 1000000 then "\(. / 100000 | floor / 10)M" elif . >= 1000 then "\(. / 100 | floor / 10)k" else tostring end;
    (.columns // 80) as $cols
    | .tasks[]
    | (title(.id) + if .name then " (\(.name))" else "" end) as $who
    | ({completed: "work complete", failed: "slain", killed: "dismissed"}[.status] // "") as $fate
    | (" · \(.tokenCount | tokens)" + if $fate == "" then "" else " · \($fate)" end) as $tail
    | ($cols - ($who | length) - 3 - ($tail | length)) as $room
    | (.label // .description // "") as $task
    | (if ($task | length) > $room then $task[:([$room - 1, 0] | max)] + "…" else $task end) as $task
    | {id, content: "\(paint($who)) · \($task)\($tail)"}'
}

status() {
  jq -r "$UNIT"'
    .session_id as $id
    | ({model: .model.id, effort: .effort.level} | paint(title($id))) as $who
    | (.context_window.used_percentage // 0 | floor | [., 100] | min) as $food
    | (if $food > 80 then [31, "High"] elif $food > 50 then [33, "Low"] else [32, "No"] end) as [$color, $upkeep]
    | "\($who) · Food \($food)/100 \u001b[\($color)m\($upkeep) Upkeep\u001b[0m"'
}

case "${1:-}" in
  hook) hook ;;
  agents) agents ;;
  status) status ;;
  *)
    echo "usage: wc3.sh hook|agents|status" >&2
    exit 2
    ;;
esac
