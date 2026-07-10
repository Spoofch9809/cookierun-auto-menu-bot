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
- **Mac .app distribution**: a downloaded (quarantined) .app is
  Gatekeeper-translocated to a random read-only path on launch, so the
  Windows zip's "config.json/templates next to the binary" layout
  silently loads DEFAULTS for downloaders. The Mac build bakes both into
  the bundle and seeds `~/Library/Application Support/CookieRun Bot` at
  startup (cookierun_gui.py top / build_mac.sh). Local test builds are
  NOT quarantined, so this failure only reproduces on a real download.
- **pywin32 in frozen builds**: PyInstaller bundles the pywin32 .pyds
  into win32// pythonwin/ subfolders and relies on a runtime hook to put
  them on sys.path -- PyInstaller 6.21 + hooks-contrib 2026.6 silently
  dropped that hook, shipping a v1.4.2 exe whose window backend died
  with "No module named 'win32gui'". Win32Backend.__init__ now appends
  those _MEIPASS subdirs itself; after any tooling upgrade, verify a
  fresh exe can actually start the win32 backend before releasing.
- **Release asset naming**: the Windows in-app updater in
  v1.3.0-v1.4.0 downloads the first release asset whose name starts
  with "CookieRunAutoMenuBot" and ends in ".zip". The Mac zip must
  therefore keep its "Mac-" prefix FIRST
  (`Mac-CookieRunAutoMenuBot-vX.Y.Z.zip`, see build_mac.sh) -- when it
  was named with the shared prefix it sorted before the Windows zip and
  broke Update Now for every existing install.
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

## State as of 2026-07-11

- Latest release: **v1.4.3** (Windows zip only). v1.4.0 shipped the
  boost-sync overhaul; v1.4.1 fixed Update Now grabbing the Mac zip;
  v1.4.2 made preserved configs gain new default buttons; v1.4.3 fixed
  the frozen exe's pywin32 imports (v1.4.2's window backend was dead)
  and added the adb-connect port fallback to serial detection.
- Windows/PC: fully working at v1.4.3 (frozen win32 backend verified
  end-to-end against live LDPlayer). "No online device" on the PC is
  expected -- LDPlayer runs with ADB debugging off (anti-cheat), and the
  window backend needs no serial.
- **RESOLVED 2026-07-11 -- Mac MuMu detection** (BlueStacks Air remains
  out: game blocks it with "Rooted Environment Detected"). Root cause:
  MuMu Pro on Mac ignores its "default port (5555)" setting and listens
  on a DYNAMIC per-instance port (observed 26624) that never registers
  with the adb server, so `adb devices` stayed empty and the 5555/7555/
  16384 connect fallback missed it. Fixes (on the Mac working copy,
  needs commit to `mac` + merge to main):
  1. `_mumu_mac_ports()` in cookierun_bot.py: lsof-scans the running
     "MuMu Android Device" process's listening TCP ports (tried before
     ADB_COMMON_PORTS; Android Device process ports before the
     MuMuPlayer shell's 20000/21000, which never speak ADB).
  2. `detect_adb_serial()` rewritten: adb start-server first (20s), then
     per-port try/except -- the old whole-scan `except: return None`
     aborted everything when the first adb call spent >5s spawning the
     server daemon. Checks devices after EACH connect and stops at the
     first online serial.
  3. 26624 added to ADB_COMMON_PORTS; Mac MuMu preset serial is now
     127.0.0.1:26624 (last-known-good hint only -- Detect finds the real
     one).
  Verified on the Mac: cold-start Detect finds 127.0.0.1:26624 in ~5s
  and AdbBackend.capture() returns live 1600x900 game frames -- so the
  game TOLERATES an active adb connection on MuMu Pro Mac (anti-cheat
  step-1 gate passed, at least when connecting after launch).
