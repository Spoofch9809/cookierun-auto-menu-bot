"""
Diagnostic: capture whatever's on screen right now (via the backend in
config.json) and save it to debug_shots/. Use this to grab a screen you
want to turn into a new/updated template.

Usage:  py capture_shot.py
"""
import cookierun_bot as bot

cfg = bot.load_config()
backend = bot.make_backend(cfg, log=print)
img = backend.capture()

if img is None:
    print("capture failed -- check config.json backend settings")
else:
    fn = bot.save_debug_shot(img, cfg)
    print(f"saved {fn}  ({img.size[0]}x{img.size[1]})")
