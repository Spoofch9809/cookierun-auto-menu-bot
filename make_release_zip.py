"""
Bundles the built exe + config.json + templates/ into one ZIP, ready to
attach to a GitHub Release. Run this after build.bat.

Usage:  py make_release_zip.py
"""
import os
import zipfile

import cookierun_bot as bot

EXE_NAME = "CookieRunAutoMenuBot.exe"


def main():
    if not os.path.exists(EXE_NAME):
        print(f"{EXE_NAME} not found -- run build.bat first.")
        return

    out_name = f"CookieRunAutoMenuBot-v{bot.APP_VERSION}.zip"
    with zipfile.ZipFile(out_name, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(EXE_NAME, EXE_NAME)
        zf.write("config.json", "config.json")
        for root, _dirs, files in os.walk("templates"):
            for fn in files:
                path = os.path.join(root, fn)
                zf.write(path, path)

    print(f"wrote {out_name}")
    print("upload this file as the binary asset on the GitHub release.")


if __name__ == "__main__":
    main()
