"""
Diagnostic: capture the current screen with whatever backend config.json
points at, and print how well it matches every template in templates/.

Use this to check whether the existing (ADB-built) templates still work
now that captures come from the win32 backend. Sit on a specific menu
screen in the game, then run this -- repeat for each screen you care
about (Lobby, Shop before/after buff, Result, Mystery Box, Gift Confirm,
Revive).

Usage:  py check_state.py
"""
import cookierun_bot as bot

cfg = bot.load_config()
backend = bot.make_backend(cfg, log=print)
img = backend.capture()

if img is None:
    print("capture failed -- check config.json backend settings")
else:
    print(f"captured {img.size[0]}x{img.size[1]} via backend={cfg['backend']}")
    det = bot.Detector(cfg, log=print)
    state, scores = det.detect_state(img)
    threshold = cfg["state_match_threshold"]
    print(f"detected: {state}  (pass threshold < {threshold})")
    print()
    for name, score in sorted(scores.items(), key=lambda kv: kv[1]):
        mark = "PASS" if score <= threshold else "    "
        print(f"  {mark}  {name:14s} {score:6.1f}")
