# Conductor Companion

A menu bar indicator and an iPhone app for the coding agents running in [Conductor](https://conductor.build) on
your Mac. See what every agent is doing, get pinged when one needs you, and message any chat from your phone —
your code never leaves the Mac.

> Unofficial. It reads Conductor's local database and uses Claude Code hooks and cross-session messaging, so a
> Conductor or Claude Code update can break it.

## What's in here

| Path | What it is |
| --- | --- |
| `mac/menubar/agent-status.py` | [SwiftBar](https://github.com/swiftbar/SwiftBar) plugin: animated status icon, every session grouped by state, themes (Warcraft III, Dragon Ball, Pokémon), sounds and phone controls |
| `mac/server/` | The phone server (`server.py`, port 47823) and its web app (`app.html`): pairing, sessions, workspaces, transcripts, replies |
| `mac/hooks/agent-status.sh` | Claude Code hook: records what each session is doing and when it needs you |
| `mac/hooks/phone-relay.sh`, `phone-relay.py` | Away mode: holds an agent's question until you answer it from the phone |
| `mac/hooks/wc3.sh` | Themed voice lines for hook events (see `mac/sounds/`) |
| `ios/` | The native iOS 27 app (SwiftUI, Liquid Glass) |

## How it works

```
 Conductor ─ claude sessions ─┬─ hooks ──────────► ~/.claude/agent-status/sessions   (what each chat is doing)
                              └─ conductor.db ───► read-only: status, unread, model, transcripts, workspaces
                                         │
                       menu bar plugin ◄─┤
                                         └─► phone server :47823 ◄──── iPhone app / web app
                                                  │
              reply to a waiting question ────────┤ inbox file → phone-relay hook → the agent
              message any other chat ─────────────┘ headless Claude → SendMessage (cross-session) → the agent
```

- **Reading** comes from Conductor's database (status, unread, model, context, transcripts) plus the hook files
  (blocked on you, current tool).
- **Writing** uses Claude Code's own cross-session messaging: the server starts a throwaway headless Claude
  (Haiku, `SendMessage` only, no hooks) that passes your text on, which wakes an idle chat. Conductor stops a
  chat's agent after 30 minutes idle; open the chat once in Conductor to bring it back.
- Conductor doesn't display messages that didn't come from its own composer, so agents are asked to quote phone
  messages ("From your phone: …") at the top of their reply.

## Install

Mac:

```sh
brew install --cask swiftbar && brew install jq qrencode
./mac/install.sh
```

iPhone:

```sh
brew install xcodegen
cd ios && xcodegen && open Agents.xcodeproj    # or ./scripts/install-on-phone.sh with the phone connected
```

Then on the Mac choose **Phone → Reachable over this Wi-Fi** and **Pair a phone…** in the menu bar, and scan the
code from the app. For access away from home, sign in to Tailscale on both and tick **Reach from anywhere**.

## The iPhone app

- **Active**: what needs you, what's working, what finished — with today's spend, turns and tool calls on top.
  Long-press a chat to mark it seen or snooze it for an hour.
- **Chats**: the live transcript, what the agent is doing right now, one-tap answers to its questions, quick
  replies, dictation, and photos from the library or camera (saved on the Mac; the agent opens them). The
  **Changes** button shows the workspace's diff against its base branch, each file's diff, the pull request and
  its checks.
- **Workspaces**: every workspace by repository, like Conductor's sidebar; **+** starts a new task (opens
  Conductor's composer on the Mac with your prompt).
- **Live Activity and Dynamic Island** while agents work, **widgets** for the Home and Lock Screen, and an
  **away-mode toggle** for Control Center.
- **Siri and Shortcuts**: "Message an agent in Agents", "Open managua in Agents", away mode.
- **Notifications** when a chat finishes, fails or asks something — reply from the lock screen or your watch.
- **Face ID** lock after a minute in the background (Mac tab), and an iPad sidebar layout.

### Push notifications

Notifications and Live Activity updates while the app is closed come from the Mac through Apple's push service,
which needs a key from your developer account:

1. In [Certificates, Identifiers & Profiles → Keys](https://developer.apple.com/account/resources/authkeys/list),
   create a key with **Apple Push Notifications service (APNs)** and download the `.p8` file.
2. Put it somewhere private, e.g. `~/.claude/agent-status/phone/AuthKey_ABC123DEFG.p8`.
3. Create `~/.claude/agent-status/phone/apns.json`:

   ```json
   {"keyId": "ABC123DEFG", "teamId": "C233ZYQ8WJ", "keyPath": "~/.claude/agent-status/phone/AuthKey_ABC123DEFG.p8",
    "bundleId": "com.j4cku.agents"}
   ```

4. In the app, turn on **Notifications** in the Mac tab. Builds from Xcode register as development tokens and the
   server sends them to Apple's sandbox automatically.

Without it everything else works; the app just updates while it's open.

## Security

The phone can make your agents act, and Conductor's agents often run with permission checks off. Pairing is
one-time (QR secret or a 6-digit code, 5 tries, 10 minutes) and every request needs the device token. Wi-Fi access
is plain HTTP — use it on networks you trust, or Tailscale. Revoke a phone from the menu bar (⌥ on its row).
