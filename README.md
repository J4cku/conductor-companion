# Conductor Companion

A menu bar indicator for the coding agents running in [Conductor](https://conductor.build) on your Mac: see what
every agent is doing, get a themed nudge when one needs you, and jump straight to it.

> Unofficial. It reads Conductor's local database and uses Claude Code hooks, so a Conductor or Claude Code update
> can break it.
>
> The phone server and iOS app that used to live here were retired once Conductor's own app could reach local
> workspaces. They're in the git history (last commit with them: `4958663`).

## What's in here

| Path | What it is |
| --- | --- |
| `mac/menubar/agent-status.py` | [SwiftBar](https://github.com/swiftbar/SwiftBar) plugin: animated status icon, every session grouped by state, themes (Warcraft III, Dragon Ball, Pokémon), sounds and mute |
| `mac/hooks/agent-status.sh` | Claude Code hook: records what each session is doing and when it needs you |
| `mac/hooks/wc3.sh` | Themed voice lines for hook events (see `mac/sounds/`) |

## How it works

```
 Conductor ─ claude sessions ─┬─ hooks ──────────► ~/.claude/agent-status/sessions   (what each chat is doing)
                              └─ conductor.db ───► read-only: status, unread, model, context, workspaces
                                         │
                       menu bar plugin ◄─┘
```

- Conductor's database says which sessions are working, idle or unread, and with which model.
- The hook files add what only hooks see: a session blocked on you, and the tool it is running.
- Clicking a session (or ⌃⌥W for the next one that needs you) opens it in Conductor.
- Plan usage: Claude's 5-hour and weekly windows (the numbers `/usage` shows, read with Claude Code's own sign-in every
  5 minutes) and Codex's windows from its newest session log. A window past 80% also shows next to the menu bar icon.
- The menu bar icon shows how much of Claude's plan is left (of the fuller window): Master Ball above 90% left, then Ultra
  (70%), Great (40%), Poké Ball; a 7- down to 1-star Dragon Ball; or a Warcraft III coin going from gold to red.

## Install

```sh
brew install --cask swiftbar && brew install jq
./mac/install.sh
```

`install.sh` is re-runnable. On a Mac that had the phone server, it also stops it and removes its hook.
