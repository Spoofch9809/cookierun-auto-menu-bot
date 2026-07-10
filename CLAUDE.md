# Cookie Run Auto Menu Bot

Automates the boring menu clicking between manually-played Cookie Run runs
(end-of-round cleanup, shop, boost selection, restart) on an Android
emulator. The human plays every level; the bot only acts when a menu
screen goes still. Windows + LDPlayer/MuMu today; macOS port is in
progress on the `mac` branch (see MAC.md).

## Layout

- `cookierun_bot.py` -- the engine: capture/input backends, template
  detection, per-state click handlers, the Bot polling loop, GitHub
  update check + in-app self-update.
- `cookierun_gui.py` -- Tkinter GUI (Controls + Coordinate Tuning tabs).
- `config.json` -- all tunables AND live bot state: button coordinates
  (as % of screen), boost selections, and the bot's boost memory. This
  file is written at runtime by the app itself.
- `templates/` -- reference screenshots per detected state. Variants:
  `STATE__anything.png` all count as STATE.
- `MAC.md` -- run-from-source-on-macOS guide + the two-machine git
  workflow.
- `RELEASING.md` -- release steps. `build.bat` builds the exe + zip.

## Safety invariants (do not weaken)

- Guard states `REVIVE` / `WAIT_USER` are never clicked (revive costs
  crystals).
- Anything not matching a known template within threshold is `UNKNOWN`
  and never clicked.
- Multi-Buy spends real in-game currency -- treat re-buy logic changes
  with care.

## Hard-won lessons (do not relearn these the hard way)

- **Testing handle()/sync logic**: `save_config()` writes to the real
  `config.json` regardless of which dict you pass (deepcopy does NOT
  protect the file). Always monkeypatch first in test scripts:
  `bot.save_config = lambda cfg, path=None: None`. This has silently
  clobbered the user's live settings twice.
- **MuMu window capture** (`Win32Backend`): the render surface is nested
  two levels deep (`Android Device` -> `MuMuNxDevice` ->
  `nemuwin`/`nemudisplay`) -- see `CHILD_CHAINS`. Capture reads the leaf,
  but posted mouse input MUST target `MuMuNxDevice` (`INPUT_HOP`): the
  leaf is a pure GPU surface that silently swallows clicks.
- **Shop direct-buy tiles** (`shop_boost_*`: HP Extension, Power Jelly
  Boost, Double XP): pixel-detecting their active state is unreliable --
  the yellow "active" fill of one tile bleeds into its neighbor's crop
  with literally identical RGB values. Synced via memory instead
  (`config["shop_boost_state"]` = what the bot last set each tile to),
  which is auto-reset on every GUI open (plus a manual Reset button)
  because the memory goes stale whenever anything other than the bot
  taps a tile. Sync order matches the GUI: HP Extension -> Power Jelly
  Boost -> Double XP.
- **MULTI_BUY checkboxes** (`boost_*`): the opposite conclusion -- these
  are synced by a live green-checkmark screen read (`_is_checked_at`)
  on every visit, NOT memory. Memory here caused a stuck-forever bug
  (the Random Boost box can reset the popup's real checkboxes
  independent of memory).
- **SHOP_READY re-buy**: `config["multi_buy_active"]` records the exact
  selection the last Multi-Buy was bought for. On SHOP_READY, if it's
  None or differs from the current selection, the bot re-buys rather
  than playing with an unverified boost (user's explicit choice: prefer
  spending coins over silently playing with the wrong boost).
- **Anti-cheat**: the game refuses to launch while ADB debugging is
  enabled -- that's the entire reason the win32 window-capture backend
  exists on Windows. The Mac port has no window backend, so MAC.md's
  step 1 (does the game tolerate ADB-on in a Mac emulator?) gates the
  whole port.

## Git workflow (two machines)

- `main` = released, Windows-tested code. The PC works in the
  NAS-synced folder -- ONLY the PC ever runs git there (a second machine
  on the same synced `.git` corrupts it).
- `mac` = Mac working branch in a separate local clone on the Mac.
  Sit-down: `git checkout mac && git fetch origin && git merge
  origin/main`. Leave: commit + `git push`.
- Releasing from the PC: `git merge origin/mac` into main, re-test on
  Windows, then RELEASING.md flow (bump `APP_VERSION`, build.bat,
  commit, tag, push, publish GitHub release with the zip).

## State as of 2026-07-10

- Latest release: **v1.3.2** (in-app self-update working end-to-end).
- **Not yet committed/released** (exists only in the Windows PC's
  working tree): the entire boost-selection sync overhaul -- direct-buy
  tile sync + ordering, live MULTI_BUY checkbox sync, SHOP_READY
  mismatch re-buy, Reset Boost Memory button, auto-reset on GUI open,
  `ENTERED_LEAGUE`-era changes are released; the boost work is not. Do
  NOT re-implement any of this on the Mac -- it lands on `main` with the
  next Windows release.
- Mac port: nothing started beyond MAC.md and the `mac` branch. Next
  action is MAC.md step 1 (emulator + ADB-on launch test), then running
  from source with the ADB backend.
