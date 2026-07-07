# Cookie Run Auto Menu Bot

Plays Cookie Run manually? This just handles the boring parts: end-of-round
cleanup and restarting into a new run (including picking a boost in the
Shop), while you play the actual level yourself. It never touches the
revive screen (costs crystals) or anything it doesn't recognize.

This automates parts of the game against its Terms of Service. Use at your
own risk -- account action is possible even though a human plays every run.

## Get it

Download the latest `CookieRunAutoMenuBot-vX.Y.Z.zip` from
[Releases](https://github.com/Spoofch9809/cookierun-auto-menu-bot/releases/latest),
extract it anywhere, and run `CookieRunAutoMenuBot.exe`. No Python install
needed.

Requirements:
- Windows
- LDPlayer, with **ADB debugging turned off** (Settings -> Other settings).
  The game's anti-cheat blocks launching with it on; the app uses a
  different capture method that doesn't need it.
- The LDPlayer window has to stay visible (not minimized) while the bot
  runs, and the game resolution should be 960x540 for the built-in
  templates to line up.

## Using it

1. Set `ADB serial` isn't actually needed for the default win32 backend --
   just leave the defaults and open the **Coordinate Tuning** tab if any
   button ends up misaligned on your setup.
2. Pick which boosts you want the bot to select in the Shop's "Pick desired
   Boosts!" popup (checkboxes on the Controls tab). Leave all unchecked to
   skip buying anything and just go straight to Play.
3. Debug mode captures menu screens without clicking (useful for building
   new templates); Run mode actually detects and clicks.
4. Hit Start, then play a level yourself. The bot stays dormant while the
   screen is moving (i.e. while you're playing) and only acts once a menu
   screen goes still.

If the bot gets stuck idling on a screen it doesn't recognize (character
select background, a different episode's art, etc.), capture it with
**Save Screenshot** and see `RELEASING.md` / ask whoever maintains this repo
to add it as a template variant.

## Updating

The app checks this repo for new releases on startup and shows a banner if
one's available -- click Download to grab it. It never auto-installs
anything for you; you still just extract the new zip over the old one (or
into a fresh folder) and keep your existing `config.json`/`templates/` if
you like your current tuning, or take the new ones bundled in the release.

## Repo layout (if you're building from source)

- `config.json` -- all tunables: backend, thresholds, button coordinates,
  which boosts to select, etc. Editable from the GUI or by hand.
- `cookierun_bot.py` -- the engine: capture backends, state detection, the
  bot loop. This is the file that changes when the bot's behavior changes.
- `cookierun_gui.py` -- the Tkinter GUI. Rarely needs to change.
- `templates/` -- reference screenshots used to recognize each menu screen.
  A state can have multiple variants (e.g. per episode background) named
  `STATE__label.png` -- see any existing `LOBBY__*.png` for the pattern.
- `build.bat` -- rebuilds the exe and packages the release zip.
- `make_release_zip.py` -- bundles the exe + config.json + templates/ into
  the zip that build.bat produces.

Running from source needs `py -m pip install pillow numpy pywin32`, then
`py cookierun_gui.py`.
