#!/bin/bash
# Builds the Pokémon and English Dragon Ball voice packs into ~/.claude/sounds/<theme>/<hook event>/ from community
# packs in the PeonPing registry. The audio is not in this repo: it belongs to its owners, so fetch it yourself.
set -euo pipefail
command -v ffmpeg >/dev/null || { echo "needs ffmpeg (brew install ffmpeg)"; exit 1; }
D="$HOME/.claude/sounds"; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
git clone -q --depth 1 --branch v1.0.0 https://github.com/devauxbr/pokemon-gen1 "$T/pk"
git clone -q --depth 1 --branch v1.0.0 https://github.com/alexlupu3/openpeon-vegeta-dbz "$T/vg"

cry() { local dir="$D/pkmn/$1"; shift; mkdir -p "$dir"; for p in "$@"; do ffmpeg -v error -y -i "$T/pk/sounds/$p.ogg" "$dir/$p.wav"; done; }
cry SessionStart bulbasaur charmander squirtle; cry Stop pikachu; cry Notification psyduck; cry StopFailure gengar
cry UserPromptSubmit primeape; cry PermissionDenied arbok; cry PreCompact snorlax
cry SubagentStart/peon pikachu; cry SubagentStart/grunt charizard; cry SubagentStart/farseer mewtwo
cry SubagentStart/brewmaster snorlax; cry SubagentStart/recruit magikarp

line() { mkdir -p "$D/dbz-en/$1"; jq -r --arg c "$2" '.categories[$c].sounds[].file' "$T/vg/openpeon.json" | while read -r f; do cp "$T/vg/$f" "$D/dbz-en/$1/"; done; }
line SessionStart session.start; line Notification input.required; line Stop task.complete; line StopFailure task.error
line UserPromptSubmit user.spam; line PreCompact resource.limit; line SubagentStart task.acknowledge; line PermissionDenied user.spam
echo "built $D/pkmn and $D/dbz-en"
