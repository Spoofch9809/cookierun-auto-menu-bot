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

import glob
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
APP_VERSION = "1.3.1"

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

# Glob patterns checked (in order) when auto-detecting each emulator's
# bundled adb.exe -- install paths vary by version/edition, so this is a
# best-effort search, not a guarantee. Used by find_adb_path(), which the
# GUI's "Detect" button calls so a fresh install doesn't need the exact
# path hand-typed.
ADB_PATH_GLOBS = {
    "ld": [
        r"C:\LDPlayer\LDPlayer*\adb.exe",
        r"C:\Program Files\LDPlayer\LDPlayer*\adb.exe",
    ],
    "mumu": [
        r"C:\Program Files\Netease\MuMuPlayer\nx_main\adb.exe",
        r"C:\Program Files\Netease\MuMuPlayer*\**\adb.exe",
        r"C:\Program Files (x86)\Netease\MuMuPlayer*\**\adb.exe",
    ],
}


def find_adb_path(emulator):
    """Best-effort search of common install locations for this emulator's
    bundled adb.exe. Returns the first existing match, or None."""
    for pattern in ADB_PATH_GLOBS.get(emulator, []):
        for path in sorted(glob.glob(pattern, recursive=True)):
            if os.path.isfile(path):
                return path
    return None


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


def _resolve_child_chain(win32gui, hwnd, chain):
    """Walks each (class_name, window_text) hop and returns the list of
    hwnds resolved along the way (same length as chain), or None if any
    hop fails."""
    hwnds = []
    cur = hwnd
    for class_name, window_text in chain:
        cur = win32gui.FindWindowEx(cur, 0, class_name, window_text)
        if not cur:
            return None
        hwnds.append(cur)
    return hwnds


def _find_render_chain(win32gui, window_title, chain):
    """Resolve a chain of nested child windows down to the emulator's
    render surface, returning the hwnd at every hop. Tries the configured
    window_title first (fast path), then -- since the exact title can
    differ by locale/emulator version/multi-instance naming while the
    internal class-name chain tends not to -- falls back to scanning every
    visible top-level window for one whose children match the chain."""
    tried = set()
    if window_title:
        top = win32gui.FindWindow(None, window_title)
        if top:
            tried.add(top)
            hwnds = _resolve_child_chain(win32gui, top, chain)
            if hwnds:
                return hwnds

    candidates = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            candidates.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    for top in candidates:
        if top in tried:
            continue
        hwnds = _resolve_child_chain(win32gui, top, chain)
        if hwnds:
            return hwnds
    return None


# ==============================================================
#  Backend: win32 (optional -- only imports pywin32 if selected)
# ==============================================================
class Win32Backend:
    # Each entry is a chain of (class_name, window_text) hops from the
    # top-level window down to the actual render surface -- FindWindowEx
    # only searches *immediate* children, so a surface nested more than one
    # level deep (e.g. MuMu's Android Device -> MuMuNxDevice -> nemudisplay)
    # needs one hop per level. Either side of a pair can be None to match
    # on just the other.
    CHILD_CHAINS = {
        "ld": [("RenderWindow", "TheRender")],
        "mumu": [(None, "MuMuNxDevice"), ("nemuwin", "nemudisplay")],
    }

    # Index (into the resolved hop list above) of the hwnd that should
    # actually receive posted mouse messages. Usually this is the same
    # window we capture pixels from (-1, the leaf), but MuMu's leaf
    # (nemuwin/nemudisplay) is a pure GPU presentation surface that
    # silently swallows WM_LBUTTONDOWN/UP -- verified live: posting to it
    # produces zero screen change, while posting the identical click one
    # hop up (MuMuNxDevice, the actual Qt window) works.
    INPUT_HOP = {
        "ld": -1,
        "mumu": -2,
    }

    def __init__(self, config, log=print):
        import win32gui, win32con, win32api, win32ui
        import ctypes
        self.g, self.c, self.a, self.ui = win32gui, win32con, win32api, win32ui
        self.ctypes = ctypes
        self.config = config
        self.log = log
        emulator = config["emulator"]
        chain = self.CHILD_CHAINS[emulator]
        hwnds = _find_render_chain(win32gui, config.get("window_title"), chain)
        if not hwnds:
            raise RuntimeError(
                f"render window (inner game surface) not found -- tried window_title="
                f"{config.get('window_title')!r} plus a scan of all visible windows. "
                f"Make sure the emulator is open and its window isn't minimized."
            )
        self.hwnd = hwnds[-1]
        self.input_hwnd = hwnds[self.INPUT_HOP[emulator]]

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
        # x,y are relative to the capture surface (self.hwnd); the window
        # that actually receives clicks (self.input_hwnd) can be a
        # different window with a different origin/size, so re-map through
        # screen coordinates rather than assuming they line up.
        screen_pt = self.g.ClientToScreen(self.hwnd, (x, y))
        tx, ty = self.g.ScreenToClient(self.input_hwnd, screen_pt)
        lparam = self.a.MAKELONG(tx, ty)
        self.g.PostMessage(self.input_hwnd, self.c.WM_LBUTTONDOWN, self.c.MK_LBUTTON, lparam)
        time.sleep(random.uniform(*self.config["hold_ms"]) / 1000.0)
        self.g.PostMessage(self.input_hwnd, self.c.WM_LBUTTONUP, 0, lparam)


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
def _is_checked_at(img, x_pct, y_pct, half_width_pct=3.0, half_height_pct=3.0):
    """Best-effort check for whether a green checkmark icon is present at
    this % coordinate -- used so we never blindly tap a boost checkbox that
    the game already remembers as checked from a previous round (that would
    just toggle it back off)."""
    w, h = img.size
    cx, cy = x_pct / 100.0 * w, y_pct / 100.0 * h
    x1 = max(0, int(cx - half_width_pct / 100.0 * w))
    x2 = min(w, int(cx + half_width_pct / 100.0 * w))
    y1 = max(0, int(cy - half_height_pct / 100.0 * h))
    y2 = min(h, int(cy + half_height_pct / 100.0 * h))
    if x2 <= x1 or y2 <= y1:
        return False
    arr = np.asarray(img.crop((x1, y1, x2, y2)).convert("RGB")).astype(int)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    mask = (g > 140) & (g - r > 30) & (g - b > 30)
    return bool(mask.mean() > 0.05)


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
    elif state == "ENTERED_LEAGUE":
        log("Entered League -> Confirm")
        backend.tap(*buttons["entered_league_confirm"])
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
        # The boost checkboxes toggle on tap, and the game remembers your
        # selection across rounds -- so a boost can already be checked the
        # very first time we see this popup on a given visit. Only tap a
        # box if it's actually unchecked right now (checked via the pixels,
        # not guessed), otherwise tapping it would just uncheck it.
        boost_keys = config.get("selected_boost_buttons", [])
        cur = backend.capture()
        tapped = 0
        already_checked = 0
        for boost_key in boost_keys:
            if boost_key not in buttons:
                log(f"selected boost '{boost_key}' has no coordinate in config.buttons -- skipping it "
                    f"(use the Coordinate Tuning tab to save it)")
                continue
            x_pct, y_pct = buttons[boost_key]
            if cur is not None and _is_checked_at(cur, x_pct, y_pct):
                already_checked += 1
                continue
            backend.tap(x_pct, y_pct)
            time.sleep(0.3)
            tapped += 1
        if tapped == 0 and already_checked == 0:
            log("Pick Boosts popup -> no valid boosts selected -- not clicking Multi-Buy")
            return
        log(f"Pick Boosts popup -> {tapped} tapped, {already_checked} already checked -> Multi-Buy")
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
    """Returns (latest_version, download_page_url, asset_zip_url) if a newer
    release is published on GitHub, or None if up to date / offline /
    anything went wrong. asset_zip_url is None if the release has no zip
    asset attached (e.g. a source-only tag) -- callers should fall back to
    opening download_page_url in that case. Never raises."""
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = data.get("tag_name", "")
        page_url = data.get("html_url", f"https://github.com/{repo}/releases/latest")
        if latest and _parse_version(latest) > _parse_version(current_version):
            asset_url = None
            for asset in data.get("assets", []):
                name = asset.get("name", "")
                if name.lower().startswith("cookierunautomenubot") and name.lower().endswith(".zip"):
                    asset_url = asset.get("browser_download_url")
                    break
            return latest, page_url, asset_url
    except Exception:
        pass
    return None


def _swap_file(path, new_bytes):
    """Writes new_bytes to `path`, first moving anything already there
    aside as `<path>.old`. Renaming a locked/running file works on
    Windows (the loader opens it with FILE_SHARE_DELETE) -- overwriting
    its contents in place does not, since the running process still has
    it mapped. Verified live: renaming this app's own exe out from under
    itself while it's running leaves the process completely unaffected
    and immediately frees the original name up for the new file."""
    old_path = path + ".old"
    if os.path.exists(old_path):
        try:
            os.remove(old_path)
        except OSError:
            pass  # still locked from an update before last -- harmless, left for next cleanup
    if os.path.exists(path):
        os.rename(path, old_path)
    with open(path, "wb") as f:
        f.write(new_bytes)


def cleanup_old_update_files(app_dir, exe_name="CookieRunAutoMenuBot.exe"):
    """Best-effort removal of '<file>.old' leftovers from a previous
    update. The old exe/icon can't be deleted until the process that had
    it open has fully exited, which isn't guaranteed the moment the new
    one launches -- so this runs on startup instead, once it's safe. A
    no-op (and safe to call every startup) when there's nothing to clean."""
    for name in (exe_name, "icon.ico"):
        old_path = os.path.join(app_dir, name + ".old")
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass


def apply_update(asset_zip_url, app_dir, exe_name="CookieRunAutoMenuBot.exe", log=print, timeout=30):
    """Downloads the release zip and swaps the new exe (+ icon) into place
    via _swap_file, then launches it. Returns True once the new exe has
    been launched (the caller should then close this process) or False on
    failure (logged; nothing on disk touched in that case).

    An earlier version of this used an external relauncher .bat (wait for
    this PID to exit, copy files over, self-delete) instead of an in-place
    rename. Dropped after live testing: antivirus silently deleted the
    generated script mid-run, since "drop an exe, launch it, self-delete"
    matches a common dropper heuristic. The rename approach needs no
    helper script at all, so there's nothing for AV to flag."""
    import zipfile

    exe_path = os.path.join(app_dir, exe_name)
    icon_path = os.path.join(app_dir, "icon.ico")

    try:
        log(f"downloading update from {asset_zip_url} ...")
        req = urllib.request.Request(asset_zip_url, headers={"Accept": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            zip_bytes = resp.read()

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = set(zf.namelist())
            if exe_name not in names:
                log(f"update failed: {exe_name} not found inside the downloaded zip")
                return False
            new_exe_bytes = zf.read(exe_name)
            new_icon_bytes = zf.read("icon.ico") if "icon.ico" in names else None

        _swap_file(exe_path, new_exe_bytes)
        if new_icon_bytes is not None:
            _swap_file(icon_path, new_icon_bytes)

        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            [exe_path],
            cwd=app_dir,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        log("update installed -- relaunching...")
        return True
    except Exception as e:
        log(f"update failed: {e}")
        return False
