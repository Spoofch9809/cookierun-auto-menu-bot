#!/bin/zsh
# macOS counterpart of build.bat: rebuilds "CookieRun Bot.app" from the
# current source and packages Mac-CookieRunAutoMenuBot-vX.Y.Z.zip, ready
# to attach to the GitHub release.
#
# The zip name deliberately does NOT start with "CookieRunAutoMenuBot":
# the Windows in-app updater in v1.3.0-v1.4.0 picks the first release
# asset with that prefix, and the Mac zip sorting first broke Update Now
# for every Windows install. Keep the "Mac-" prefix FIRST.
#
# Unlike the Windows zip, config.json and templates/ are baked INTO the
# .app: Gatekeeper translocation runs a downloaded .app from a random
# read-only path, so "files next to the app" silently breaks for
# downloaders. At runtime the app seeds them into
# ~/Library/Application Support/CookieRun Bot (see cookierun_gui.py).
#
# Needs: pip install pyinstaller. The build is Apple-Silicon-only (a
# PyInstaller app runs on the CPU family it was built on) and unsigned,
# so downloaders need the one-time Gatekeeper "Open Anyway" dance.
set -e
cd "$(dirname "$0")"

# .icns app icon, generated fresh from ginger-biscuit.png each build.
rm -rf build/mac_icon.iconset
mkdir -p build/mac_icon.iconset
for sz in 16 32 128 256 512; do
    sips -z $sz $sz ginger-biscuit.png --out "build/mac_icon.iconset/icon_${sz}x${sz}.png" >/dev/null
    dbl=$((sz * 2))
    if [ $dbl -le 512 ]; then
        sips -z $dbl $dbl ginger-biscuit.png --out "build/mac_icon.iconset/icon_${sz}x${sz}@2x.png" >/dev/null
    fi
done
iconutil -c icns build/mac_icon.iconset -o build/CookieRunBot.icns

# Bake in this machine's tuned config, but back in debug (save-only)
# mode and with the machine-specific boost memory cleared.
rm -rf build/mac_shipcfg
mkdir -p build/mac_shipcfg
python3 - build/mac_shipcfg/config.json <<'EOF'
import json, sys
cfg = json.load(open("config.json"))
cfg["mode"] = "debug"
cfg["shop_boost_state"] = {}
cfg["multi_buy_active"] = None
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
EOF

python3 -m PyInstaller --windowed --name "CookieRun Bot" \
    --icon "$(pwd)/build/CookieRunBot.icns" \
    --add-data "$(pwd)/build/mac_shipcfg/config.json:." \
    --add-data "$(pwd)/templates:templates" \
    --distpath dist --workpath build --specpath build \
    --noconfirm \
    cookierun_gui.py

VERSION=$(python3 -c "import cookierun_bot; print(cookierun_bot.APP_VERSION)")
ZIP="Mac-CookieRunAutoMenuBot-v${VERSION}.zip"
STAGE="build/mac_zip/CookieRunAutoMenuBot-Mac"
rm -rf build/mac_zip "$ZIP"
mkdir -p "$STAGE"
cp -R "dist/CookieRun Bot.app" "$STAGE/"
cat > "$STAGE/README.txt" <<'EOF'
CookieRun Auto Menu Bot -- macOS (Apple Silicon)

1. Drag "CookieRun Bot.app" anywhere you like (e.g. Applications).
   Settings and screenshots live in
   ~/Library/Application Support/CookieRun Bot -- delete that folder to
   factory-reset the app.
2. First launch: the app is unsigned, so macOS will say it can't verify
   it is free of malware. One-time fix:
     - Double-click the app, click "Done" (NOT "Move to Trash").
     - System Settings -> Privacy & Security -> scroll down ->
       "Open Anyway" next to the CookieRun Bot message, authenticate.
   (On macOS 14 or older, right-click the app -> Open -> Open works too.)
3. Requires MuMuPlayer Pro with ADB debugging enabled. In the app, hit
   "Detect" next to ADB path, then "Save to config.json".
4. The app starts in Debug mode (screenshots only, no clicking). Switch
   Mode to "Run" once detection looks right.
EOF
# ditto preserves the .app structure/permissions correctly (plain zip
# can break bundle executables).
ditto -c -k --keepParent "build/mac_zip/CookieRunAutoMenuBot-Mac" "$ZIP"

echo
echo "Build OK: $ZIP"
echo "Attach it to the GitHub release:  gh release upload v${VERSION} $ZIP"
