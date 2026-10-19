# Sound packs

`hooks/wc3.sh` plays a random file from `~/.claude/sounds/<theme>/<hook event>/`, where `<theme>` is the menu bar
theme (`wc3`, `dbz`, `pkmn`) and falls back to `wc3`. `SubagentStart/<unit>/` holds per-model voices
(`peon` = Haiku or low effort, `grunt` = Sonnet, `farseer` = Opus, `brewmaster` = Fable, `recruit` = unknown) and
falls back to `SubagentStart/`. Events: `SessionStart`, `Stop`, `StopFailure`, `Notification`, `UserPromptSubmit`
(4 prompts in a minute), `PermissionDenied`, `PreCompact`, `SubagentStart`.

No audio is committed here — it is game and anime audio that belongs to its owners.

- **wc3**: Warcraft III peon packs from [PeonPing/og-packs](https://github.com/PeonPing/og-packs).
- **pkmn** and **dbz-en**: run `./build-packs.sh` (Gen 1 cries, English Vegeta lines from the PeonPing registry).
- **dbz** (Japanese Goku/Vegeta): the voice sets from *Jump Force* on The Sounds Resource. Download them yourself,
  then drop the clips you like into the event folders. Good picks: Goku's 「オッス、オラ悟空」 for `SessionStart`,
  「かめはめ波」 and Vegeta's 「きたねぇ花火だ」 for `Stop`, 「オラに元気分けてくれ」 for `Notification`.

Muting: the menu bar's **Sounds** submenu writes `~/.claude/agent-status/mute`, which `wc3.sh` honours.
