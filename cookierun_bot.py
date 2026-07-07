r"""
Cookie Run Auto Menu Bot -- engine module.

Config lives in config.json (editable, survives updates to this file).
This module is the "update this one file" piece: backends, state
detection, and the threaded Bot loop. The GUI (cookierun_gui.py)
drives it and never needs to change when the engine does.

Safety invariants (do not weaken):
  - REVIVE is checked before anything else in detect_state() and is
    never clicked (costs crystals).
  - Anything that doesn't match a known template within threshold is
    UNKNOWN and is never clicked.
"""

import io
import json
import os
import random
import subprocess
import threading
import time
import urllib.request

import numpy as np
from PIL import Image

# Bump this each time you rebuild the packaged app (see build.bat) so the
# GUI's title bar shows which build is actually running, and so the update
# checker can tell a new release apart from what's currently installed.
APP_VERSION = "1.1.0"

# Public repo used for update checks -- see build.bat for how a new release
# gets published there.
GITHUB_REPO = "Spoofch9809/cookierun-auto-menu-bot"

CONFIG_PATH = "config.json"

DEFAULTS = {
    "backend": "adb",
    "window_title": "LDPlayer",
    "emulator": "ld",
    "adb_path": r"C:\LDPlayer\LDPlayer14\adb.exe",
    "adb_serial": "emulator-5554",
    "mode": "debug",
    "debug_dir": "debug_shots",
    "verbose": True,
    "templates_dir": "templates",
    "state_match_threshold": 20.0,
    "state_region": {},
    "poll_interval": 0.5,
    "static_frames_needed": 3,
    "static_threshold": 2.5,
    "action_cooldown": 1.5,
    "jitter_pct": 2.0,
    "hold_ms": [40, 90],
    "pre_delay": [0.05, 0.15],
    "selected_boost_buttons": ["boost_double_coins"],
    "buttons": {},
}

# States that must never be clicked, no matter what.
GUARD_STATES = {"REVIVE", "WAIT_USER"}


# ==============================================================
#  Config load/save
# ==============================================================
def load_config(path=CONFIG_PATH):
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            on_disk = json.load(f)
        cfg.update(on_disk)
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ==============================================================
#  Backend: ADB
# ==============================================================
class AdbBackend:
    def __init__(self, config, log=print):
        self.adb_path = config["adb_path"]
        self.serial = config["adb_serial"]
        self.log = log
        self.config = config

    def _adb(self, *args, capture=False):
        cmd = [self.adb_path, "-s", self.serial, *args]
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        if capture:
            return subprocess.run(cmd, capture_output=True, creationflags=creationflags).stdout
        return subprocess.run(cmd, creationflags=creationflags)

    def capture(self):
        try:
            raw = self._adb("exec-out", "screencap", "-p", capture=True)
            return Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception as e:
            self.log(f"capture failed: {e}")
            return None

    def tap(self, x_pct, y_pct):
        img = self.capture()
        if img is None:
            return
        w, h = img.size
        x, y = _jitter_to_px(x_pct, y_pct, w, h, self.config)
        time.sleep(random.uniform(*self.config["pre_delay"]))
        hold = random.randint(*self.config["hold_ms"])
        self._adb("shell", "input", "swipe", str(x), str(y), str(x), str(y), str(hold))


# ==============================================================
#  Backend: win32 (optional -- only imports pywin32 if selected)
# ==============================================================
class Win32Backend:
    def __init__(self, config, log=print):
        import win32gui, win32con, win32api, win32ui
        import ctypes
        self.g, self.c, self.a, self.ui = win32gui, win32con, win32api, win32ui
        self.ctypes = ctypes
        self.config = config
        self.log = log
        self.child = {"ld": ("RenderWindow", "TheRender"),
                      "mumu": ("subWin", "sub")}[config["emulator"]]
        parent = win32gui.FindWindow(None, config["window_title"])
        if not parent:
            raise RuntimeError(f"window '{config['window_title']}' not found")
        self.hwnd = win32gui.FindWindowEx(parent, 0, *self.child)
        if not self.hwnd:
            raise RuntimeError("render window (inner game surface) not found")

    def _size(self):
        _, _, r, b = self.g.GetClientRect(self.hwnd)
        return r, b

    def capture(self):
        try:
            w, h = self._size()
            if w <= 0 or h <= 0:
                return None
            hdc = self.g.GetWindowDC(self.hwnd)
            mfc = self.ui.CreateDCFromHandle(hdc)
            save = mfc.CreateCompatibleDC()
            bmp = self.ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(mfc, w, h)
            save.SelectObject(bmp)
            self.ctypes.windll.user32.PrintWindow(self.hwnd, save.GetSafeHdc(), 2)
            info = bmp.GetInfo()
            img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                                    bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
            self.g.DeleteObject(bmp.GetHandle())
            save.DeleteDC(); mfc.DeleteDC(); self.g.ReleaseDC(self.hwnd, hdc)
            return img
        except Exception as e:
            self.log(f"capture failed: {e} (if you get a black image, switch to backend=adb)")
            return None

    def tap(self, x_pct, y_pct):
        w, h = self._size()
        if w <= 0 or h <= 0:
            return
        x, y = _jitter_to_px(x_pct, y_pct, w, h, self.config)
        time.sleep(random.uniform(*self.config["pre_delay"]))
        lparam = self.a.MAKELONG(x, y)
        self.g.PostMessage(self.hwnd, self.c.WM_LBUTTONDOWN, self.c.MK_LBUTTON, lparam)
        time.sleep(random.uniform(*self.config["hold_ms"]) / 1000.0)
        self.g.PostMessage(self.hwnd, self.c.WM_LBUTTONUP, 0, lparam)


def make_backend(config, log=print):
    if config["backend"] == "adb":
        return AdbBackend(config, log=log)
    return Win32Backend(config, log=log)


# ==============================================================
#  Screen-state detection
# ==============================================================
def _jitter_to_px(x_pct, y_pct, w, h, config):
    jitter = config["jitter_pct"]
    px = x_pct + random.uniform(-jitter, jitter)
    py = y_pct + random.uniform(-jitter, jitter)
    x = max(0, min(int(w * px / 100.0), w - 1))
    y = max(0, min(int(h * py / 100.0), h - 1))
    return x, y


def _signature(img, region=None):
    """Downscaled grayscale signature (160x90), optionally cropped to a % region first."""
    if region:
        w, h = img.size
        x1, y1, x2, y2 = region
        img = img.crop((int(w * x1 / 100), int(h * y1 / 100),
                         int(w * x2 / 100), int(h * y2 / 100)))
    return np.asarray(img.resize((160, 90)).convert("L"), dtype=np.float32)


def _diff(sig_a, sig_b):
    return float(np.mean(np.abs(sig_a - sig_b)))


def frame_diff(a, b):
    return _diff(_signature(a), _signature(b))


class Detector:
    """Holds the loaded templates so they aren't re-read from disk every frame."""

    def __init__(self, config, log=print):
        self.config = config
        self.log = log
        self.templates = {}
        self.reload_templates()

    def reload_templates(self):
        # A state can have more than one reference image (e.g. LOBBY looks
        # different per episode background) -- name variants
        # "STATE__anything.png" and they all count as that state, matched by
        # whichever variant scores best.
        self.templates = {}
        templates_dir = self.config["templates_dir"]
        if os.path.isdir(templates_dir):
            for root, _dirs, files in os.walk(templates_dir):
                for fn in files:
                    name, ext = os.path.splitext(fn)
                    if ext.lower() in (".png", ".jpg", ".jpeg"):
                        state = name.split("__")[0].upper()
                        img = Image.open(os.path.join(root, fn)).convert("RGB")
                        self.templates.setdefault(state, []).append(img)
        return len(self.templates)

    def match_scores(self, img):
        state_region = self.config.get("state_region", {})
        scores = {}
        for name, variants in self.templates.items():
            region = state_region.get(name)
            sig = _signature(img, region)
            scores[name] = min(_diff(sig, _signature(t, region)) for t in variants)
        return scores

    def detect_state(self, img):
        scores = self.match_scores(img)
        if not scores:
            return "UNKNOWN", scores

        threshold = self.config["state_match_threshold"]

        # Guard states are checked first, independent of what "best" would pick.
        for guard in GUARD_STATES:
            if scores.get(guard, 1e9) <= threshold:
                return guard, scores

        best_state = min(scores, key=scores.get)
        if scores[best_state] <= threshold:
            return best_state, scores
        return "UNKNOWN", scores


# ==============================================================
#  State handlers (what to click for each detected state)
# ==============================================================
def handle(backend, state, config, log):
    if state in GUARD_STATES:
        log(f"guard state {state} -> not clicking (left for you to handle manually)")
        return

    buttons = config["buttons"]

    if state == "RESULT":
        log("Result -> OK")
        backend.tap(*buttons["ok"])
    elif state == "MYSTERY_BOX":
        log("Mystery Box -> Open all")
        backend.tap(*buttons["open_all"])
    elif state == "GIFT_CONFIRM":
        log("Reward -> Confirm")
        backend.tap(*buttons["confirm"])
    elif state == "LEVEL_UP":
        log("Level Up -> Confirm")
        backend.tap(*buttons["confirm"])
    elif state == "DAILY_CHECKIN":
        log("Daily Check-in (calendar) -> OK")
        backend.tap(*buttons["daily_checkin_ok"])
    elif state == "DAILY_CHECKIN_CONFIRM":
        log("Daily Check-in (reward) -> Confirm")
        backend.tap(*buttons["daily_checkin_confirm"])
    elif state == "LOBBY":
        log("Lobby -> Play!")
        backend.tap(*buttons["lobby_play"])
    elif state == "SHOP_START":
        # Whatever item was last selected in the grid (could be a leftover
        # HP/speed upgrade pick, not the Random Boost box), re-select the
        # Random Boost box first so the rest of the flow is always acting
        # on the right item.
        backend.tap(*buttons["shop_random_box"])
        boost_keys = [k for k in config.get("selected_boost_buttons", []) if k in buttons]
        if not boost_keys:
            log("Shop (no buff yet) -> no boosts selected -- skip buying, go straight to Play!")
            time.sleep(0.4)
            backend.tap(*buttons["shop_play"])
            return
        log("Shop (no buff yet) -> select Random Boost box + open Multi")
        time.sleep(0.4)
        backend.tap(*buttons["multi_tab"])
    elif state == "MULTI_BUY":
        boost_keys = config.get("selected_boost_buttons", [])
        tapped = 0
        for boost_key in boost_keys:
            if boost_key not in buttons:
                log(f"selected boost '{boost_key}' has no coordinate in config.buttons -- skipping it "
                    f"(use the Coordinate Tuning tab to save it)")
                continue
            backend.tap(*buttons[boost_key])
            time.sleep(0.3)
            tapped += 1
        if tapped == 0:
            log("Pick Boosts popup -> no valid boosts selected -- not clicking Multi-Buy")
            return
        log(f"Pick Boosts popup -> selected {tapped} boost(s) -> Multi-Buy")
        backend.tap(*buttons["multi_buy"])
    elif state == "SHOP_READY":
        log("Shop (buff ready) -> Play!")
        backend.tap(*buttons["shop_play"])
    else:
        log(f"unhandled state {state} -- not clicking")


def save_debug_shot(img, config):
    debug_dir = config["debug_dir"]
    os.makedirs(debug_dir, exist_ok=True)
    fn = os.path.join(debug_dir, f"screen_{time.strftime('%H%M%S')}.png")
    img.save(fn)
    return fn


# ==============================================================
#  Bot: runs the capture/detect/click loop on a background thread
# ==============================================================
class Bot:
    def __init__(self, config, log=print):
        self.config = config
        self.log = log
        self.backend = None
        self.detector = None
        self._thread = None
        self._stop_event = threading.Event()
        self.running = False

    def start(self):
        if self.running:
            return True
        try:
            self.backend = make_backend(self.config, log=self.log)
        except Exception as e:
            self.log(f"failed to start backend: {e}")
            return False

        prev = self.backend.capture()
        if prev is None:
            self.log("capture failed -- check adb_serial / instance running / adb_path")
            return False

        self.detector = Detector(self.config, log=self.log)
        n = len(self.detector.templates)
        self.log(f"templates loaded: {n}" + (" (none found -- run in debug mode first)" if n == 0 else ""))
        self.log(f"started (backend={self.config['backend']}, screen {prev.size[0]}x{prev.size[1]}, mode={self.config['mode']})")

        self._stop_event.clear()
        self.running = True
        self._thread = threading.Thread(target=self._loop, args=(prev,), daemon=True)
        self._thread.start()
        return True

    def stop(self):
        if not self.running:
            return
        self._stop_event.set()
        self.running = False
        self.log("stopping...")

    def _loop(self, prev):
        stable = 0
        last_action = 0.0

        while not self._stop_event.is_set():
            time.sleep(self.config["poll_interval"])
            cur = self.backend.capture()
            if cur is None:
                continue

            d = frame_diff(prev, cur)
            stable = stable + 1 if d < self.config["static_threshold"] else 0
            prev = cur
            if self.config["verbose"]:
                self.log(f"   frame diff={d:5.1f}  (static<{self.config['static_threshold']})  stable={stable}")

            if stable < self.config["static_frames_needed"]:
                continue
            if time.time() - last_action < self.config["action_cooldown"]:
                continue

            if self.config["mode"] == "debug":
                fn = save_debug_shot(cur, self.config)
                self.log(f"stable screen -> saved {fn}")
                last_action = time.time()
                stable = 0
                continue

            state, scores = self.detector.detect_state(cur)

            if self.config["verbose"]:
                top = sorted(scores.items(), key=lambda kv: kv[1])[:3]
                detail = "  ".join(f"{n}={v:.1f}" for n, v in top)
                self.log(f"stable -> guess: {state}  (closest: {detail}  threshold<{self.config['state_match_threshold']})")

            if state == "UNKNOWN":
                continue

            handle(self.backend, state, self.config, self.log)
            last_action = time.time()
            stable = 0

        self.log("stopped")


# ==============================================================
#  Update check (GitHub Releases) -- never raises, never blocks;
#  callers should run this on a background thread.
# ==============================================================
def _parse_version(v):
    v = v.strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def check_for_update(current_version=APP_VERSION, repo=GITHUB_REPO, timeout=5):
    """Returns (latest_version, download_page_url) if a newer release is
    published on GitHub, or None if up to date / offline / anything went
    wrong. Never raises."""
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = data.get("tag_name", "")
        page_url = data.get("html_url", f"https://github.com/{repo}/releases/latest")
        if latest and _parse_version(latest) > _parse_version(current_version):
            return latest, page_url
    except Exception:
        pass
    return None
