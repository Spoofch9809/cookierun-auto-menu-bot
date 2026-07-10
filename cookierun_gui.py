r"""
Cookie Run Auto Menu Bot -- GUI.

Two tabs:
  Controls          -- mode toggle, start/stop, verbose, key fields, live log.
  Coordinate Tuning  -- capture a live screenshot, click a point to read its
                        x%/y% (assign to a button), or drag a box to read a
                        region (assign to a state's match region), and save
                        straight into config.json. No more guessing from
                        uploaded screenshots.

Only stdlib (tkinter) + pillow/numpy (already required by the engine).
"""

import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox

from PIL import ImageTk

# Anchor to the app's own folder so config.json/templates/debug_shots are
# always found next to the exe (or this script), regardless of whatever
# directory the app happened to be launched from.
_IS_FROZEN = getattr(sys, "frozen", False)
if _IS_FROZEN and sys.platform == "darwin":
    # "Files next to the app" doesn't work on Mac: Gatekeeper translocates
    # a downloaded (quarantined) .app to a random read-only path on launch,
    # so anything sitting beside the real bundle is invisible. Instead the
    # .app carries config.json + templates/ inside it (build_mac.sh) and
    # live state goes to Application Support, seeded on first run.
    _app_dir = os.path.expanduser("~/Library/Application Support/CookieRun Bot")
    os.makedirs(_app_dir, exist_ok=True)
    _bundled = sys._MEIPASS
    if not os.path.exists(os.path.join(_app_dir, "config.json")):
        shutil.copy(os.path.join(_bundled, "config.json"), _app_dir)
    # Top up templates a newer release added (including subfolders like
    # templates/lobby/); never overwrite existing ones (they may be
    # locally re-captured for this user's setup).
    _src_templates = os.path.join(_bundled, "templates")
    for _root, _dirs, _files in os.walk(_src_templates):
        _rel = os.path.relpath(_root, _src_templates)
        _dst_dir = os.path.join(_app_dir, "templates", _rel)
        os.makedirs(_dst_dir, exist_ok=True)
        for _fn in _files:
            if not os.path.exists(os.path.join(_dst_dir, _fn)):
                shutil.copy(os.path.join(_root, _fn), _dst_dir)
elif _IS_FROZEN:
    _app_dir = os.path.dirname(sys.executable)
else:
    _app_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(_app_dir)

import cookierun_bot as bot

if _IS_FROZEN:
    # Sweep up a "<exe>.old" left behind by a previous in-place update --
    # see apply_update()/cleanup_old_update_files() in cookierun_bot.py.
    # No-op almost every launch; only does anything the run right after
    # an update.
    bot.cleanup_old_update_files(_app_dir)

THEME = {
    "bg": "#f7ecd8",        # cream dough
    "bg_panel": "#efe0c0",  # slightly darker cream for tab/scrollbar chrome
    "text": "#5b3a24",      # chocolate brown
    "accent": "#6fbf4f",    # Cookie Run's own button green
    "accent_dark": "#57a23c",
    "entry_bg": "#fffdf6",
    "border": "#c9a876",    # tan/gold
}


def _apply_theme(root):
    """Cream/brown/green skin loosely matching Cookie Run's own UI. Built on
    the 'clam' base theme because Windows' native theme (vista/winnative)
    ignores most ttk color overrides -- clam actually respects them."""
    t = THEME
    root.configure(bg=t["bg"])

    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=t["bg"], foreground=t["text"],
                     fieldbackground=t["entry_bg"], bordercolor=t["border"],
                     focuscolor=t["accent"])
    style.configure("TFrame", background=t["bg"])
    style.configure("TLabel", background=t["bg"], foreground=t["text"])
    style.configure("TLabelframe", background=t["bg"], bordercolor=t["border"])
    style.configure("TLabelframe.Label", background=t["bg"], foreground=t["text"],
                     font=("TkDefaultFont", 9, "bold"))
    style.configure("TCheckbutton", background=t["bg"], foreground=t["text"])
    style.map("TCheckbutton", background=[("active", t["bg"])])
    style.configure("TRadiobutton", background=t["bg"], foreground=t["text"])
    style.map("TRadiobutton", background=[("active", t["bg"])])
    style.configure("TEntry", fieldbackground=t["entry_bg"], foreground=t["text"])
    style.configure("TCombobox", fieldbackground=t["entry_bg"], foreground=t["text"])
    style.configure("TButton", background=t["accent"], foreground="white",
                     bordercolor=t["accent_dark"], padding=5)
    style.map("TButton",
              background=[("active", t["accent_dark"]), ("disabled", "#d8d0c0")],
              foreground=[("disabled", "#998f7c")])
    style.configure("TNotebook", background=t["bg"], bordercolor=t["border"])
    style.configure("TNotebook.Tab", background=t["bg_panel"], foreground=t["text"], padding=(10, 4))
    style.map("TNotebook.Tab", background=[("selected", t["bg"])])
    style.configure("Vertical.TScrollbar", background=t["bg_panel"], troughcolor=t["bg"],
                     bordercolor=t["border"])
    style.configure("Horizontal.TScrollbar", background=t["bg_panel"], troughcolor=t["bg"],
                     bordercolor=t["border"])

KNOWN_STATES = [
    "LOBBY", "SHOP_START", "MULTI_BUY", "SHOP_READY", "LEVEL_UP",
    "RESULT", "MYSTERY_BOX", "GIFT_CONFIRM", "DAILY_CHECKIN", "DAILY_CHECKIN_CONFIRM",
    "ENTERED_LEAGUE", "REVIVE", "WAIT_USER",
]

# Defaults filled into window_title / adb_path / adb_serial when the user
# switches emulators on the Controls tab. adb_path in particular varies by
# emulator version/install location -- these are just sensible starting
# points, editable (and saved) right below the radio buttons.
EMULATOR_PRESETS = {
    "ld": {
        "window_title": "LDPlayer",
        "adb_path": r"C:\LDPlayer\LDPlayer14\adb.exe",
        "adb_serial": "emulator-5554",
    },
    "mumu": {
        "window_title": "Android Device",
        "adb_path": r"C:\Program Files\Netease\MuMuPlayer\nx_main\adb.exe",
        "adb_serial": "127.0.0.1:7555",
    },
}

if sys.platform == "darwin":
    # MuMuPlayer Pro (the macOS edition) is a 127.0.0.1:<port> TCP endpoint
    # like Windows MuMu, but the port is dynamic per instance (observed:
    # 26624) and never registers with the adb server on its own -- Detect
    # scans the running MuMu process's listening ports (bot._mumu_mac_ports)
    # and adb-connects, so this serial is only the last-known-good starting
    # point. window_title is irrelevant on Mac (no Window backend) but kept
    # for config symmetry.
    EMULATOR_PRESETS["mumu"] = {
        "window_title": "Android Device",
        "adb_path": ("/Applications/MuMuPlayer Pro.app/Contents/MacOS/"
                     "MuMu Android Device.app/Contents/MacOS/tools/adb"),
        "adb_serial": "127.0.0.1:26624",
    }

BOOST_BUTTON_PREFIX = "boost_"

# Temporary: only these are considered tested/ready. Other boosts show up
# (greyed out) but can't be checked yet -- remove an entry here once
# you've verified that boost's SHOP_READY detection actually works.
ENABLED_BOOSTS = {"boost_double_coins", "boost_magnetic_aura"}

# Direct-buy tiles in SHOP_START's own "Buy some Boosts!" panel (Double XP /
# HP Extension / Power Jelly Boost) -- a separate purchase flow from the
# boost_* checkboxes above (those live in the MULTI_BUY popup reached via
# the Random Boost box + Multi tab). Kept as a distinct prefix/list
# (selected_shop_boosts) so the two flows don't get tangled together.
SHOP_BOOST_PREFIX = "shop_boost_"
ENABLED_SHOP_BOOSTS = {"shop_boost_double_xp", "shop_boost_hp_extension", "shop_boost_power_jelly_boost"}

# Display order for the direct-buy checkboxes -- matches the order asked
# for, not alphabetical. Anything not listed here (e.g. a new one added
# later via Coordinate Tuning) just sorts alphabetically after these.
SHOP_BOOST_ORDER = ["shop_boost_hp_extension", "shop_boost_power_jelly_boost", "shop_boost_double_xp"]


def _humanize_boost_name(name):
    for prefix in (BOOST_BUTTON_PREFIX, SHOP_BOOST_PREFIX):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    words = [("HP" if w.lower() == "hp" else w.capitalize()) for w in name.split("_")]
    return " ".join(words)

MAX_CANVAS_W = 900
MAX_CANVAS_H = 560
CLICK_DRAG_THRESHOLD_PX = 6  # below this, a drag is treated as a click


class App:
    def __init__(self, root):
        self.root = root
        root.title(f"Cookie Run Auto Menu Bot v{bot.APP_VERSION}")
        root.geometry("880x640")
        root.minsize(540, 560)
        if os.path.exists("icon.ico"):
            try:
                root.iconbitmap("icon.ico")
            except tk.TclError:
                pass
        _apply_theme(root)

        self.config = bot.load_config()
        # The bot's remembered boost state (shop_boost_state /
        # multi_buy_active) only stays accurate while the bot is the only
        # thing tapping those toggles. Across app sessions that doesn't
        # hold -- the game may have been restarted or tiles tapped by hand
        # in between -- so every GUI open starts from a clean slate, same
        # as pressing the Reset button. (The Reset button still exists for
        # mid-session manual fiddling.)
        if self.config.get("shop_boost_state") or self.config.get("multi_buy_active"):
            bot.reset_boost_memory(self.config)
        self.log_queue = queue.Queue()
        self.bot = bot.Bot(self.config, log=self.log_queue.put)

        self.mode_var = tk.StringVar(value=self.config["mode"])
        self.verbose_var = tk.BooleanVar(value=self.config["verbose"])
        self.emulator_var = tk.StringVar(value=self.config.get("emulator", "ld"))
        self.backend_var = tk.StringVar(value=self.config.get("backend", "win32"))
        self.window_title_var = tk.StringVar(value=self.config.get("window_title", ""))
        self.adb_path_var = tk.StringVar(value=self.config.get("adb_path", ""))
        self.adb_serial_var = tk.StringVar(value=self.config["adb_serial"])
        self.state_threshold_var = tk.StringVar(value=str(self.config["state_match_threshold"]))
        self.static_threshold_var = tk.StringVar(value=str(self.config["static_threshold"]))
        self.status_var = tk.StringVar(value="stopped")
        self.boost_vars = {}  # button name -> BooleanVar, built in _rebuild_boost_checkboxes
        self.shop_boost_vars = {}  # button name -> BooleanVar, built in _rebuild_shop_boost_checkboxes

        self.update_banner = None  # built lazily in _show_update_banner
        self._update_url = None

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True)

        controls_tab = ttk.Frame(notebook)
        tuning_tab = ttk.Frame(notebook)
        notebook.add(controls_tab, text="Controls")
        notebook.add(tuning_tab, text="Coordinate Tuning")

        self._build_controls_tab(controls_tab)
        self._build_tuning_tab(tuning_tab)

        self.root.after(100, self._poll_log_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        threading.Thread(target=self._check_for_update_worker, daemon=True).start()

        if _IS_FROZEN:
            # The module-level cleanup_old_update_files() call up top
            # usually loses this race: right after an update relaunches
            # the app, the previous process is often still mid-shutdown
            # and still holds "<exe>.old" open, so that first attempt
            # silently no-ops. Retry once more a few seconds in, by which
            # point the old process has had time to fully exit -- avoids
            # leaving the leftover sitting there until the next full
            # restart.
            self.root.after(5000, lambda: bot.cleanup_old_update_files(_app_dir))

    def _check_for_update_worker(self):
        result = bot.check_for_update()
        if result:
            latest, page_url, asset_url = result
            self.root.after(0, lambda: self._show_update_banner(latest, page_url, asset_url))

    def _show_update_banner(self, latest_version, page_url, asset_url):
        # Plain tk widgets (not ttk) so the highlight color reliably shows
        # up regardless of the active ttk theme.
        self._update_url = page_url
        self._update_asset_url = asset_url
        banner = tk.Frame(self.root, bg="#fff3cd", padx=8, pady=6)
        self._update_label_var = tk.StringVar(
            value=f"Update available: {latest_version} (you have v{bot.APP_VERSION})")
        tk.Label(banner, textvariable=self._update_label_var, bg="#fff3cd").pack(side="left")
        # Update Now needs a real exe on disk to replace -- only offer it
        # for the packaged app (not "py cookierun_gui.py" from source), and
        # only if the release actually has a zip asset attached.
        if _IS_FROZEN and asset_url:
            self.update_now_btn = ttk.Button(banner, text="Update Now", command=self._on_update_now)
            self.update_now_btn.pack(side="left", padx=(8, 0))
        ttk.Button(banner, text="Download", command=self._on_download_update).pack(side="left", padx=(8, 0))
        self.update_dismiss_btn = ttk.Button(banner, text="Dismiss", command=banner.destroy)
        self.update_dismiss_btn.pack(side="left", padx=(4, 0))
        banner.pack(fill="x", before=self.root.winfo_children()[0])
        self.update_banner = banner

    def _on_download_update(self):
        if self._update_url:
            webbrowser.open(self._update_url)

    def _on_update_now(self):
        self.update_now_btn.configure(state="disabled")
        self.update_dismiss_btn.configure(state="disabled")
        self._update_label_var.set("Downloading update...")

        def worker():
            ok = bot.apply_update(self._update_asset_url, _app_dir, log=self._log)
            self.root.after(0, lambda: self._after_update_apply(ok))

        threading.Thread(target=worker, daemon=True).start()

    def _after_update_apply(self, ok):
        if ok:
            # The relauncher is already waiting for this process to exit --
            # close everything down so it can copy the new exe in and
            # start it back up.
            self._on_close()
            return
        self._update_label_var.set("Update download failed (see log) -- click Update Now to retry, "
                                    "or use Download instead")
        self.update_now_btn.configure(state="normal")
        self.update_dismiss_btn.configure(state="normal")

    # ----------------------------------------------------------
    #  Controls tab
    # ----------------------------------------------------------
    def _make_collapsible(self, parent, title, start_expanded=True):
        """A bordered section with a +/- toggle in its header that shows or
        hides everything below it. Returns (section, content) -- build the
        section's actual widgets inside `content`."""
        section = ttk.Frame(parent, relief="groove", borderwidth=1)
        header = ttk.Frame(section, padding=(6, 4))
        header.pack(fill="x")
        content = ttk.Frame(section, padding=8)
        if start_expanded:
            content.pack(fill="x")

        def toggle():
            if content.winfo_ismapped():
                content.pack_forget()
                toggle_btn.configure(text="+")
            else:
                content.pack(fill="x")
                toggle_btn.configure(text="−")

        toggle_btn = ttk.Button(header, text="−" if start_expanded else "+", width=2, command=toggle)
        toggle_btn.pack(side="left")
        ttk.Label(header, text=title, font=("TkDefaultFont", 9, "bold")).pack(side="left", padx=(6, 0))

        return section, content

    def _build_controls_tab(self, parent):
        # Each section below is one collapsible box containing plain,
        # unbordered "chips" that re-flow into however many columns fit the
        # window width. Keeping the border at the section level (not per
        # chip) means uneven grid-column widths never show up as visibly
        # empty boxes -- there's no border there to reveal the gap.
        run_section, run_content = self._make_collapsible(parent, "Run Controls")
        run_section.pack(fill="x", padx=8, pady=(8, 0))
        self.run_chips_frame = ttk.Frame(run_content)
        self.run_chips_frame.pack(fill="x", anchor="w")
        self._build_run_chips()
        self._run_cols = 0
        run_section.bind("<Configure>",
                          lambda e: self._reflow(self._run_chips, max(1, e.width // 165), "_run_cols"))

        settings_section, settings_content = self._make_collapsible(parent, "Key Settings")
        settings_section.pack(fill="x", padx=8, pady=(8, 0))
        self.settings_chips_frame = ttk.Frame(settings_content)
        self.settings_chips_frame.pack(fill="x", anchor="w")
        self._build_settings_chips()
        self._settings_cols = 0
        settings_section.bind("<Configure>",
                               lambda e: self._reflow(self._settings_chips, max(1, e.width // 175), "_settings_cols"))

        boost_section, boost_content = self._make_collapsible(parent, "Boosts to select in Shop (pick any number)")
        boost_section.pack(fill="x", padx=8, pady=(8, 0))
        self.boost_checks_frame = ttk.Frame(boost_content)
        self.boost_checks_frame.pack(anchor="w", fill="x")
        self._boost_cols = 1
        self._rebuild_boost_checkboxes()
        boost_section.bind("<Configure>", self._on_boost_frame_resize)
        ttk.Label(boost_content, text="add more via Coordinate Tuning tab (name must start with 'boost_')",
                  foreground="#888").pack(anchor="w", pady=(6, 0))

        shop_boost_section, shop_boost_content = self._make_collapsible(
            parent, "Direct-buy boosts in Shop screen (pick any number)")
        shop_boost_section.pack(fill="x", padx=8, pady=(8, 0))
        self.shop_boost_checks_frame = ttk.Frame(shop_boost_content)
        self.shop_boost_checks_frame.pack(anchor="w", fill="x")
        self._shop_boost_cols = 1
        self._rebuild_shop_boost_checkboxes()
        shop_boost_section.bind("<Configure>", self._on_shop_boost_frame_resize)
        ttk.Label(shop_boost_content,
                  text="add more via Coordinate Tuning tab (name must start with 'shop_boost_')",
                  foreground="#888").pack(anchor="w", pady=(6, 0))

        file_frame = ttk.Frame(parent, padding=(8, 8))
        file_frame.pack(fill="x")
        ttk.Button(file_frame, text="Open config.json", command=self._on_open_config).pack(side="left")
        ttk.Button(file_frame, text="Reload config.json", command=self._on_reload_config).pack(side="left", padx=(6, 0))

        log_frame = ttk.LabelFrame(parent, text="Log", padding=6)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="word",
                                 bg=THEME["entry_bg"], fg=THEME["text"],
                                 insertbackground=THEME["text"], relief="flat")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _build_run_chips(self):
        parent = self.run_chips_frame

        mode_chip = ttk.Frame(parent)
        ttk.Label(mode_chip, text="Mode", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        ttk.Radiobutton(mode_chip, text="Debug (save only, no clicks)",
                         variable=self.mode_var, value="debug",
                         command=self._on_mode_change).pack(anchor="w")
        ttk.Radiobutton(mode_chip, text="Run (detect + click)",
                         variable=self.mode_var, value="run",
                         command=self._on_mode_change).pack(anchor="w")

        bot_chip = ttk.Frame(parent)
        ttk.Label(bot_chip, text="Bot", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        btn_row = ttk.Frame(bot_chip)
        btn_row.pack(anchor="w", pady=(2, 0))
        self.start_btn = ttk.Button(btn_row, text="Start", command=self._on_start)
        self.start_btn.pack(side="left", padx=(0, 4))
        self.stop_btn = ttk.Button(btn_row, text="Stop", command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left")
        ttk.Checkbutton(bot_chip, text="Verbose", variable=self.verbose_var,
                         command=self._on_verbose_change).pack(anchor="w", pady=(2, 0))
        ttk.Label(bot_chip, textvariable=self.status_var, foreground="#555").pack(anchor="w")

        shot_chip = ttk.Frame(parent)
        ttk.Label(shot_chip, text="Screenshot", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        ttk.Button(shot_chip, text="Save Screenshot", command=self._on_save_screenshot).pack(anchor="w", pady=(2, 0))
        self.shot_status = ttk.Label(shot_chip, text="saves to debug_shots/", foreground="#888")
        self.shot_status.pack(anchor="w")

        memory_chip = ttk.Frame(parent)
        ttk.Label(memory_chip, text="Boost Memory", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        ttk.Button(memory_chip, text="Reset", command=self._on_reset_boost_memory).pack(anchor="w", pady=(2, 0))
        ttk.Label(memory_chip, text="if you tapped boosts by hand", foreground="#888").pack(anchor="w")

        self._run_chips = [mode_chip, bot_chip, shot_chip, memory_chip]

    def _build_settings_chips(self):
        parent = self.settings_chips_frame

        def field_chip(label_text, var, width=20):
            chip = ttk.Frame(parent)
            ttk.Label(chip, text=label_text).pack(anchor="w")
            ttk.Entry(chip, textvariable=var, width=width).pack(anchor="w", pady=(2, 0))
            return chip

        emulator_chip = ttk.Frame(parent)
        ttk.Label(emulator_chip, text="Emulator", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        ttk.Radiobutton(emulator_chip, text="LDPlayer", variable=self.emulator_var,
                         value="ld", command=self._on_emulator_change).pack(anchor="w")
        ttk.Radiobutton(emulator_chip, text="MuMu Player", variable=self.emulator_var,
                         value="mumu", command=self._on_emulator_change).pack(anchor="w")

        backend_chip = ttk.Frame(parent)
        ttk.Label(backend_chip, text="Capture backend", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        ttk.Radiobutton(backend_chip, text="Window (recommended)", variable=self.backend_var,
                         value="win32", command=self._on_backend_change).pack(anchor="w")
        ttk.Radiobutton(backend_chip, text="ADB", variable=self.backend_var,
                         value="adb", command=self._on_backend_change).pack(anchor="w")

        window_title_chip = field_chip("Window title:", self.window_title_var)

        adb_path_chip = ttk.Frame(parent)
        ttk.Label(adb_path_chip, text="ADB path:").pack(anchor="w")
        adb_path_row = ttk.Frame(adb_path_chip)
        adb_path_row.pack(anchor="w", pady=(2, 0))
        ttk.Entry(adb_path_row, textvariable=self.adb_path_var, width=24).pack(side="left")
        ttk.Button(adb_path_row, text="Detect", width=7, command=self._on_detect_adb).pack(side="left", padx=(4, 0))

        adb_chip = field_chip("ADB serial:", self.adb_serial_var)
        state_chip = field_chip("State match threshold:", self.state_threshold_var)
        static_chip = field_chip("Static threshold:", self.static_threshold_var)

        save_chip = ttk.Frame(parent)
        ttk.Label(save_chip, text=" ").pack(anchor="w")
        ttk.Button(save_chip, text="Save to config.json", command=self._on_save_fields).pack(anchor="w", pady=(2, 0))

        self._settings_chips = [emulator_chip, backend_chip, window_title_chip, adb_path_chip,
                                 adb_chip, state_chip, static_chip, save_chip]

    def _reflow(self, widgets, cols, state_attr):
        """Re-grid a fixed set of panels into `cols` columns, skipping the
        work if the column count hasn't actually changed since last time."""
        if getattr(self, state_attr) == cols:
            return
        setattr(self, state_attr, cols)
        for i, w in enumerate(widgets):
            w.grid(row=i // cols, column=i % cols, sticky="nw", padx=(0, 8), pady=(0, 8))

    def _log(self, msg):
        self.log_queue.put(msg)

    def _poll_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", str(msg) + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

    def _boost_button_names(self):
        return sorted(n for n in self.config.get("buttons", {}) if n.startswith(BOOST_BUTTON_PREFIX))

    def _rebuild_boost_checkboxes(self):
        for child in self.boost_checks_frame.winfo_children():
            child.destroy()
        selected = set(self.config.get("selected_boost_buttons", []))
        self.boost_vars = {}
        names = self._boost_button_names()
        cols = max(1, self._boost_cols)
        for i, name in enumerate(names):
            var = tk.BooleanVar(value=name in selected)
            self.boost_vars[name] = var
            cb = ttk.Checkbutton(self.boost_checks_frame, text=_humanize_boost_name(name), variable=var,
                                  command=lambda n=name: self._on_boost_toggle(n))
            if name not in ENABLED_BOOSTS:
                cb.state(["disabled"])
            cb.grid(row=i // cols, column=i % cols, sticky="w", padx=(0, 14), pady=1)

    def _on_boost_frame_resize(self, event):
        # re-flow the boost checkboxes into however many ~160px columns fit
        # the current window width, so nothing gets clipped when narrow and
        # space isn't wasted when wide.
        cols = max(1, event.width // 160)
        if cols != self._boost_cols:
            self._boost_cols = cols
            self._rebuild_boost_checkboxes()

    def _on_boost_toggle(self, name):
        selected = [n for n, v in self.boost_vars.items() if v.get()]
        self.config["selected_boost_buttons"] = selected
        bot.save_config(self.config)
        self._log(f"boosts to select in Shop -> {selected}")

    def _shop_boost_button_names(self):
        names = [n for n in self.config.get("buttons", {}) if n.startswith(SHOP_BOOST_PREFIX)]

        def sort_key(name):
            try:
                return (0, SHOP_BOOST_ORDER.index(name))
            except ValueError:
                return (1, name)

        return sorted(names, key=sort_key)

    def _rebuild_shop_boost_checkboxes(self):
        for child in self.shop_boost_checks_frame.winfo_children():
            child.destroy()
        selected = set(self.config.get("selected_shop_boosts", []))
        self.shop_boost_vars = {}
        names = self._shop_boost_button_names()
        cols = max(1, self._shop_boost_cols)
        for i, name in enumerate(names):
            var = tk.BooleanVar(value=name in selected)
            self.shop_boost_vars[name] = var
            cb = ttk.Checkbutton(self.shop_boost_checks_frame, text=_humanize_boost_name(name), variable=var,
                                  command=lambda n=name: self._on_shop_boost_toggle(n))
            if name not in ENABLED_SHOP_BOOSTS:
                cb.state(["disabled"])
            cb.grid(row=i // cols, column=i % cols, sticky="w", padx=(0, 14), pady=1)

    def _on_shop_boost_frame_resize(self, event):
        cols = max(1, event.width // 160)
        if cols != self._shop_boost_cols:
            self._shop_boost_cols = cols
            self._rebuild_shop_boost_checkboxes()

    def _on_shop_boost_toggle(self, name):
        selected = [n for n, v in self.shop_boost_vars.items() if v.get()]
        self.config["selected_shop_boosts"] = selected
        bot.save_config(self.config)
        self._log(f"direct-buy boosts to activate in Shop -> {selected}")

    def _on_mode_change(self):
        self.config["mode"] = self.mode_var.get()
        self._log(f"mode -> {self.config['mode']}")

    def _on_verbose_change(self):
        self.config["verbose"] = bool(self.verbose_var.get())

    def _on_emulator_change(self):
        emulator = self.emulator_var.get()
        preset = EMULATOR_PRESETS.get(emulator, {})
        detected = bot.find_adb_path(emulator)
        self.window_title_var.set(preset.get("window_title", ""))
        self.adb_path_var.set(detected or preset.get("adb_path", ""))
        self.adb_serial_var.set(preset.get("adb_serial", ""))
        self.config["emulator"] = emulator
        self.config["window_title"] = self.window_title_var.get()
        self.config["adb_path"] = self.adb_path_var.get()
        self.config["adb_serial"] = self.adb_serial_var.get()
        bot.save_config(self.config)
        found_note = "found on this PC" if detected else "guessed -- not found on this PC, use Detect or edit by hand"
        self._log(f"emulator -> {emulator} (window title / ADB serial reset to defaults; "
                   f"ADB path {found_note}: {self.adb_path_var.get()})")

    def _on_backend_change(self):
        self.config["backend"] = self.backend_var.get()
        bot.save_config(self.config)
        self._log(f"capture backend -> {self.config['backend']}")

    def _on_detect_adb(self):
        path = bot.find_adb_path(self.emulator_var.get())
        if not path:
            self._log("couldn't find adb under common install locations -- "
                       "browse your emulator's install folder and paste the path in by hand")
            return
        self.adb_path_var.set(path)
        self._log(f"found adb -> {path} (click Save to config.json to keep it)")
        self._log("looking for a connected device...")

        # `adb devices` can block for seconds while the adb server starts,
        # and the port-connect fallback adds a few more -- keep the UI alive.
        def worker():
            serial = bot.detect_adb_serial(path)

            def apply():
                if serial:
                    self.adb_serial_var.set(serial)
                    self._log(f"found connected device -> serial {serial}")
                else:
                    self._log("no online device from `adb devices` -- is the emulator "
                               "running with ADB debugging enabled?")

            self.root.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def _on_start(self):
        if not self._apply_fields_to_config():
            return
        self.start_btn.configure(state="disabled")

        def worker():
            ok = self.bot.start()
            def after_start():
                if ok:
                    self.stop_btn.configure(state="normal")
                    self.status_var.set("running")
                else:
                    self.start_btn.configure(state="normal")
                    self.status_var.set("failed to start (see log)")
            self.root.after(0, after_start)

        threading.Thread(target=worker, daemon=True).start()

    def _on_stop(self):
        self.bot.stop()
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_var.set("stopped")

    def _on_save_screenshot(self):
        if not self._apply_fields_to_config():
            return

        def worker():
            try:
                backend = self._make_capture_backend()
                img = backend.capture()
            except Exception as e:
                img = None
                self._log(f"screenshot capture failed: {e}")

            def apply():
                if img is None:
                    self.shot_status.configure(text="capture failed (see log)")
                    return
                fn = bot.save_debug_shot(img, self.config)
                self._log(f"saved screenshot -> {fn}")
                self.shot_status.configure(text=f"saved {os.path.basename(fn)}")

            self.root.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def _on_reset_boost_memory(self):
        bot.reset_boost_memory(self.config)
        self._log("boost memory reset -- next sync re-checks everything from scratch "
                   "instead of trusting what it remembered")

    def _apply_fields_to_config(self):
        """Copy the Key Settings entry fields into the live config dict, so
        the bot runs with what's visible on screen even if "Save to
        config.json" wasn't clicked. Returns False on invalid thresholds."""
        try:
            state_threshold = float(self.state_threshold_var.get())
            static_threshold = float(self.static_threshold_var.get())
        except ValueError:
            messagebox.showerror("Invalid value", "Thresholds must be numbers.")
            return False
        self.config["window_title"] = self.window_title_var.get().strip()
        self.config["adb_path"] = self.adb_path_var.get().strip()
        self.config["adb_serial"] = self.adb_serial_var.get().strip()
        self.config["state_match_threshold"] = state_threshold
        self.config["static_threshold"] = static_threshold
        return True

    def _on_save_fields(self):
        if not self._apply_fields_to_config():
            return
        bot.save_config(self.config)
        self._log("saved key settings to config.json")

    def _on_open_config(self):
        path = os.path.abspath(bot.CONFIG_PATH)
        try:
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("Could not open", str(e))

    def _on_reload_config(self):
        fresh = bot.load_config()
        self.config.clear()
        self.config.update(fresh)
        self.mode_var.set(self.config["mode"])
        self.verbose_var.set(self.config["verbose"])
        self.emulator_var.set(self.config.get("emulator", "ld"))
        self.backend_var.set(self.config.get("backend", "win32"))
        self.window_title_var.set(self.config.get("window_title", ""))
        self.adb_path_var.set(self.config.get("adb_path", ""))
        self.adb_serial_var.set(self.config["adb_serial"])
        self.state_threshold_var.set(str(self.config["state_match_threshold"]))
        self.static_threshold_var.set(str(self.config["static_threshold"]))
        self._rebuild_boost_checkboxes()
        self._rebuild_shop_boost_checkboxes()
        self._log("reloaded config.json from disk")
        self._refresh_overlay()

    def _on_close(self):
        if self.bot.running:
            self.bot.stop()
        self.root.destroy()

    # ----------------------------------------------------------
    #  Coordinate Tuning tab
    # ----------------------------------------------------------
    def _build_tuning_tab(self, parent):
        top = ttk.Frame(parent, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="Capture Screenshot", command=self._on_capture).pack(side="left")
        self.tuning_status = ttk.Label(top, text="no screenshot yet")
        self.tuning_status.pack(side="left", padx=(8, 0))

        body = ttk.Frame(parent, padding=8)
        body.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(body, width=MAX_CANVAS_W, height=MAX_CANVAS_H,
                                 background="#222", highlightthickness=1, highlightbackground="#555")
        self.canvas.pack(side="left")
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)

        side = ttk.Frame(body, padding=(12, 0))
        side.pack(side="left", fill="both", expand=True)

        ttk.Label(side, text="Last selection:").pack(anchor="w")
        self.selection_var = tk.StringVar(value="(click or drag on the screenshot)")
        ttk.Label(side, textvariable=self.selection_var, wraplength=260, justify="left").pack(anchor="w", pady=(0, 10))

        assign_button_frame = ttk.LabelFrame(side, text="Assign point to a button", padding=6)
        assign_button_frame.pack(fill="x", pady=(0, 10))
        self.button_name_var = tk.StringVar()
        self.button_combo = ttk.Combobox(assign_button_frame, textvariable=self.button_name_var,
                                          values=sorted(self.config["buttons"].keys()))
        self.button_combo.pack(fill="x")
        ttk.Button(assign_button_frame, text="Save as button coordinate",
                   command=self._on_assign_button).pack(fill="x", pady=(6, 0))

        assign_region_frame = ttk.LabelFrame(side, text="Assign box to a state's match region", padding=6)
        assign_region_frame.pack(fill="x")
        self.state_name_var = tk.StringVar()
        self.state_combo = ttk.Combobox(assign_region_frame, textvariable=self.state_name_var,
                                         values=KNOWN_STATES)
        self.state_combo.pack(fill="x")
        ttk.Button(assign_region_frame, text="Save as state region",
                   command=self._on_assign_region).pack(fill="x", pady=(6, 0))

        # capture/selection state
        self._pil_img = None       # full-res PIL image from the last capture
        self._tk_img = None        # PhotoImage kept alive for the canvas
        self._scale = 1.0
        self._drag_start = None    # (canvas_x, canvas_y)
        self._drag_rect_id = None
        self._last_point_pct = None    # (x_pct, y_pct)
        self._last_region_pct = None   # (x1, y1, x2, y2)
        self._overlay_ids = []

    def _make_capture_backend(self):
        # A throwaway backend just for grabbing a still frame, independent of
        # whether the bot loop is currently running.
        return bot.make_backend(self.config, log=self._log)

    def _on_capture(self):
        def worker():
            try:
                backend = self._make_capture_backend()
                img = backend.capture()
            except Exception as e:
                img = None
                self._log(f"tuning capture failed: {e}")

            def apply():
                if img is None:
                    self.tuning_status.configure(text="capture failed (see log)")
                    return
                self._pil_img = img
                self._render_capture()
                self.tuning_status.configure(text=f"{img.size[0]}x{img.size[1]}")

            self.root.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def _render_capture(self):
        img = self._pil_img
        w, h = img.size
        scale = min(MAX_CANVAS_W / w, MAX_CANVAS_H / h, 1.0)
        self._scale = scale
        disp_w, disp_h = max(1, int(w * scale)), max(1, int(h * scale))
        disp_img = img.resize((disp_w, disp_h))
        self._tk_img = ImageTk.PhotoImage(disp_img)
        self.canvas.delete("all")
        self.canvas.configure(width=disp_w, height=disp_h)
        self.canvas.create_image(0, 0, anchor="nw", image=self._tk_img)
        self._refresh_overlay()

    def _refresh_overlay(self):
        if self._pil_img is None:
            return
        for oid in self._overlay_ids:
            self.canvas.delete(oid)
        self._overlay_ids = []
        scale = self._scale
        for name, (xp, yp) in self.config.get("buttons", {}).items():
            cx, cy = xp / 100.0 * self._pil_img.size[0] * scale, yp / 100.0 * self._pil_img.size[1] * scale
            r = 4
            oid = self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline="lime", width=2)
            self._overlay_ids.append(oid)
            oid2 = self.canvas.create_text(cx + 6, cy - 6, text=name, fill="lime", anchor="w", font=("TkDefaultFont", 8))
            self._overlay_ids.append(oid2)
        for name, region in self.config.get("state_region", {}).items():
            x1, y1, x2, y2 = region
            iw, ih = self._pil_img.size
            cx1, cy1 = x1 / 100.0 * iw * scale, y1 / 100.0 * ih * scale
            cx2, cy2 = x2 / 100.0 * iw * scale, y2 / 100.0 * ih * scale
            oid = self.canvas.create_rectangle(cx1, cy1, cx2, cy2, outline="cyan", width=2)
            self._overlay_ids.append(oid)
            oid2 = self.canvas.create_text(cx1 + 3, cy1 + 3, text=name, fill="cyan", anchor="nw", font=("TkDefaultFont", 8))
            self._overlay_ids.append(oid2)

    def _on_canvas_press(self, event):
        if self._pil_img is None:
            return
        self._drag_start = (event.x, event.y)
        if self._drag_rect_id is not None:
            self.canvas.delete(self._drag_rect_id)
            self._drag_rect_id = None

    def _on_canvas_drag(self, event):
        if self._pil_img is None or self._drag_start is None:
            return
        sx, sy = self._drag_start
        if self._drag_rect_id is not None:
            self.canvas.delete(self._drag_rect_id)
        self._drag_rect_id = self.canvas.create_rectangle(sx, sy, event.x, event.y, outline="yellow", width=2)

    def _on_canvas_release(self, event):
        if self._pil_img is None or self._drag_start is None:
            return
        sx, sy = self._drag_start
        ex, ey = event.x, event.y
        self._drag_start = None

        w, h = self._pil_img.size
        scale = self._scale

        def to_pct(cx, cy):
            cx = max(0, min(cx, w * scale))
            cy = max(0, min(cy, h * scale))
            return (cx / scale) / w * 100.0, (cy / scale) / h * 100.0

        dist = ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5
        if dist < CLICK_DRAG_THRESHOLD_PX:
            if self._drag_rect_id is not None:
                self.canvas.delete(self._drag_rect_id)
                self._drag_rect_id = None
            x_pct, y_pct = to_pct(ex, ey)
            self._last_point_pct = (round(x_pct, 2), round(y_pct, 2))
            self._last_region_pct = None
            self.selection_var.set(f"Point: x%={x_pct:.2f}  y%={y_pct:.2f}")
        else:
            x1p, y1p = to_pct(min(sx, ex), min(sy, ey))
            x2p, y2p = to_pct(max(sx, ex), max(sy, ey))
            self._last_region_pct = (round(x1p, 2), round(y1p, 2), round(x2p, 2), round(y2p, 2))
            self._last_point_pct = None
            self.selection_var.set(
                f"Region: x1%={x1p:.2f} y1%={y1p:.2f} x2%={x2p:.2f} y2%={y2p:.2f}")

    def _on_assign_button(self):
        name = self.button_name_var.get().strip()
        if not name:
            messagebox.showerror("Missing name", "Pick or type a button name first.")
            return
        if self._last_point_pct is None:
            messagebox.showerror("No point selected", "Click a point on the screenshot first (a drag makes a region, not a point).")
            return
        self.config.setdefault("buttons", {})[name] = list(self._last_point_pct)
        bot.save_config(self.config)
        values = sorted(self.config["buttons"].keys())
        self.button_combo.configure(values=values)
        self._rebuild_boost_checkboxes()
        self._rebuild_shop_boost_checkboxes()
        self._log(f"button '{name}' -> {self._last_point_pct}")
        self._refresh_overlay()

    def _on_assign_region(self):
        name = self.state_name_var.get().strip().upper()
        if not name:
            messagebox.showerror("Missing name", "Pick or type a state name first.")
            return
        if self._last_region_pct is None:
            messagebox.showerror("No region selected", "Drag a box on the screenshot first (a plain click makes a point, not a region).")
            return
        self.config.setdefault("state_region", {})[name] = list(self._last_region_pct)
        bot.save_config(self.config)
        self._log(f"state region '{name}' -> {self._last_region_pct}")
        self._refresh_overlay()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
