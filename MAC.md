# Running on Mac (experimental, from source)

The packaged `.exe` is Windows-only. On a Mac the bot can currently only run
from source, using the **ADB backend** -- the default "Window" capture
backend is built on Windows APIs (pywin32) that don't exist on macOS.

## The workflow between machines

- `main` = the released, Windows-tested code (updated from the PC).
- `mac` = the Mac working branch. Work and commit here on the Mac.
- GitHub is the courier between the two machines. **Never run git against
  the NAS-synced copy from the Mac** -- two machines sharing one synced
  `.git` folder corrupts repositories. The Mac uses its own local clone.

Each time you sit down at the Mac:

```
git checkout mac
git fetch origin
git merge origin/main     # catch up on anything released from the PC
```

When done working on the Mac:

```
git add -A
git commit -m "mac: <what changed>"
git push                  # un-pushed commits are invisible to the PC!
```

Back on the PC, to bring Mac work into a release:

```
git checkout main
git fetch origin
git merge origin/mac
# re-test on Windows, then the normal release flow (see RELEASING.md)
```

## Step 1 -- the make-or-break test (do this before anything else)

The whole port hinges on this: the game's anti-cheat is known to block
launching while ADB debugging is enabled (that's why the Windows version
uses window capture instead). On Mac, ADB is the only backend -- so:

1. Install an Android emulator: MuMu Player Pro (paid, Apple Silicon) or
   BlueStacks Air, or Google's Android Studio emulator.
2. Turn **ADB debugging ON** in the emulator's settings.
3. Install and launch Cookie Run.

If the game refuses to launch or kicks you out: **stop -- the Mac port is
not viable** with the current approach. Report back before sinking more
time in.

## Step 2 -- Python setup

macOS ships without the needed packages. In Terminal:

```
python3 --version          # any 3.9+ is fine
python3 -m pip install pillow numpy
```

Do NOT install pywin32 -- it's Windows-only and not needed for the ADB
backend.

## Step 3 -- find the emulator's ADB endpoint

The Windows auto-detect ("Detect" button) only knows Windows install
paths, so on Mac fill these in by hand:

- **adb binary**: either bundled with the emulator (check its app folder)
  or install it yourself: `brew install android-platform-tools` -- then
  the path is just `adb`.
- **serial**: run `adb devices` with the emulator running. Whatever shows
  up (e.g. `127.0.0.1:5555` or `emulator-5554`) is the serial. If the
  list is empty, try `adb connect 127.0.0.1:<port>` with the port from
  the emulator's ADB settings screen.

## Step 4 -- run it

```
python3 cookierun_gui.py
```

In the GUI:

1. Key Settings -> **Capture backend: ADB** (the Window backend will just
   error on Mac).
2. Fill **ADB path** and **ADB serial** from step 3, hit
   **Save to config.json**.
3. Coordinate Tuning tab -> **Capture Screenshot**. If you see the game,
   capture works end-to-end.
4. Set the game resolution to 960x540 (or at least 16:9) so the bundled
   templates line up, then test in Debug mode before letting Run mode
   click anything.

## Known rough edges on Mac (fine to ignore for now)

- The **"Open config.json"** button errors (`os.startfile` is
  Windows-only). Edit the file in any editor instead.
- The **emulator picker presets** (LDPlayer / MuMu Player) set Windows
  window titles and paths -- irrelevant on Mac, only the ADB fields
  matter.
- **Update banner / Update Now** is Windows-exe-specific. On Mac you
  update by `git merge origin/main` instead.
- Coordinates and templates are resolution-independent (percentage
  based), so the Windows-tuned config.json should work as-is.
