#!/bin/zsh
# macOS counterpart of build.bat: rebuilds "CookieRun Bot.app" from the
# current source and packages CookieRunAutoMenuBot-Mac-vX.Y.Z.zip, ready
# to attach to the GitHub release. config.json / templates/ live next to
# the .app inside the zip and are read at runtime, not baked in.
#
# Needs: pip install pyinstaller. The build is Apple-Silicon-only (a
# PyInstaller app runs on the CPU family it was built on) and unsigned,
# so downloaders right-click -> Open the first time (Gatekeeper).
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

python3 -m PyInstaller --windowed --name "CookieRun Bot" \
    --icon "$(pwd)/build/CookieRunBot.icns" \
    --distpath dist --workpath build --specpath build \
    --noconfirm \
    cookierun_gui.py

VERSION=$(python3 -c "import cookierun_bot; print(cookierun_bot.APP_VERSION)")
ZIP="CookieRunAutoMenuBot-Mac-v${VERSION}.zip"
STAGE="build/mac_zip/CookieRunAutoMenuBot-Mac"
rm -rf build/mac_zip "$ZIP"
mkdir -p "$STAGE"
cp -R "dist/CookieRun Bot.app" "$STAGE/"
# Ship this machine's tuned config, but back in debug (save-only) mode
# and with the machine-specific boost memory cleared.
python3 - "$STAGE/config.json" <<'EOF'
import json, sys
cfg = json.load(open("config.json"))
cfg["mode"] = "debug"
cfg["shop_boost_state"] = {}
cfg["multi_buy_active"] = None
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
EOF
cp -R templates "$STAGE/templates"
cat > "$STAGE/README.txt" <<'EOF'
CookieRun Auto Menu Bot -- macOS (Apple Silicon)

1. Keep this folder together: the app reads config.json and templates/
   from the folder it sits in. Move the whole folder, not just the app.
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
