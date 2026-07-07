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
if getattr(sys, "frozen", False):
    _app_dir = os.path.dirname(sys.executable)
else:
    _app_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(_app_dir)

import cookierun_bot as bot

KNOWN_STATES = [
    "LOBBY", "SHOP_START", "MULTI_BUY", "SHOP_READY", "LEVEL_UP",
    "RESULT", "MYSTERY_BOX", "GIFT_CONFIRM", "REVIVE", "WAIT_USER",
]

BOOST_BUTTON_PREFIX = "boost_"


def _humanize_boost_name(name):
    label = name[len(BOOST_BUTTON_PREFIX):] if name.startswith(BOOST_BUTTON_PREFIX) else name
    words = [("HP" if w.lower() == "hp" else w.capitalize()) for w in label.split("_")]
    return " ".join(words)

MAX_CANVAS_W = 900
MAX_CANVAS_H = 560
CLICK_DRAG_THRESHOLD_PX = 6  # below this, a drag is treated as a click


class App:
    def __init__(self, root):
        self.root = root
        root.title(f"Cookie Run Auto Menu Bot v{bot.APP_VERSION}")
        root.geometry("880x640")
        root.minsize(620, 560)

        self.config = bot.load_config()
        self.log_queue = queue.Queue()
        self.bot = bot.Bot(self.config, log=self.log_queue.put)

        self.mode_var = tk.StringVar(value=self.config["mode"])
        self.verbose_var = tk.BooleanVar(value=self.config["verbose"])
        self.adb_serial_var = tk.StringVar(value=self.config["adb_serial"])
        self.state_threshold_var = tk.StringVar(value=str(self.config["state_match_threshold"]))
        self.static_threshold_var = tk.StringVar(value=str(self.config["static_threshold"]))
        self.status_var = tk.StringVar(value="stopped")
        self.boost_vars = {}  # button name -> BooleanVar, built in _rebuild_boost_checkboxes

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

    def _check_for_update_worker(self):
        result = bot.check_for_update()
        if result:
            latest, url = result
            self.root.after(0, lambda: self._show_update_banner(latest, url))

    def _show_update_banner(self, latest_version, url):
        # Plain tk widgets (not ttk) so the highlight color reliably shows
        # up regardless of the active ttk theme.
        self._update_url = url
        banner = tk.Frame(self.root, bg="#fff3cd", padx=8, pady=6)
        tk.Label(banner, text=f"Update available: {latest_version} (you have v{bot.APP_VERSION})",
                 bg="#fff3cd").pack(side="left")
        ttk.Button(banner, text="Download", command=self._on_download_update).pack(side="left", padx=(8, 0))
        ttk.Button(banner, text="Dismiss", command=banner.destroy).pack(side="left", padx=(4, 0))
        banner.pack(fill="x", before=self.root.winfo_children()[0])
        self.update_banner = banner

    def _on_download_update(self):
        if self._update_url:
            webbrowser.open(self._update_url)

    # ----------------------------------------------------------
    #  Controls tab
    # ----------------------------------------------------------
    def _build_controls_tab(self, parent):
        row1 = ttk.Frame(parent, padding=(8, 8, 8, 0))
        row1.pack(fill="x")

        mode_frame = ttk.LabelFrame(row1, text="Mode", padding=6)
        ttk.Radiobutton(mode_frame, text="Debug (save screenshots, no clicks)",
                         variable=self.mode_var, value="debug",
                         command=self._on_mode_change).pack(anchor="w")
        ttk.Radiobutton(mode_frame, text="Run (detect + click)",
                         variable=self.mode_var, value="run",
                         command=self._on_mode_change).pack(anchor="w")

        run_frame = ttk.LabelFrame(row1, text="Bot", padding=6)
        btn_row = ttk.Frame(run_frame)
        btn_row.pack(anchor="w")
        self.start_btn = ttk.Button(btn_row, text="Start", command=self._on_start)
        self.start_btn.pack(side="left", padx=(0, 4))
        self.stop_btn = ttk.Button(btn_row, text="Stop", command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left")
        ttk.Checkbutton(run_frame, text="Verbose", variable=self.verbose_var,
                         command=self._on_verbose_change).pack(anchor="w", pady=(6, 0))
        ttk.Label(run_frame, textvariable=self.status_var).pack(anchor="w", pady=(6, 0))

        shot_frame = ttk.LabelFrame(row1, text="Screenshot", padding=6)
        ttk.Button(shot_frame, text="Save Screenshot", command=self._on_save_screenshot).pack(anchor="w")
        self.shot_status = ttk.Label(shot_frame, text="saves to debug_shots/")
        self.shot_status.pack(anchor="w", pady=(6, 0))

        fields_frame = ttk.LabelFrame(row1, text="Key settings", padding=6)
        self._field_row(fields_frame, "ADB serial:", self.adb_serial_var, 0)
        self._field_row(fields_frame, "State match threshold:", self.state_threshold_var, 1)
        self._field_row(fields_frame, "Static threshold:", self.static_threshold_var, 2)
        ttk.Button(fields_frame, text="Save to config.json",
                   command=self._on_save_fields).grid(row=3, column=0, columnspan=2, pady=(6, 0), sticky="w")

        # These four panels re-flow into however many columns fit the
        # current window width, instead of a fixed side-by-side row that
        # clips when the window is narrow.
        self._row1_widgets = [mode_frame, run_frame, shot_frame, fields_frame]
        self._row1_cols = 0
        row1.bind("<Configure>", lambda e: self._reflow(self._row1_widgets, max(1, e.width // 195), "_row1_cols"))

        boost_frame = ttk.LabelFrame(parent, text="Boosts to select in Shop (pick any number)", padding=6)
        boost_frame.pack(fill="x", padx=8, pady=(8, 0))
        self.boost_checks_frame = ttk.Frame(boost_frame)
        self.boost_checks_frame.pack(anchor="w", fill="x")
        self._boost_cols = 1
        self._rebuild_boost_checkboxes()
        boost_frame.bind("<Configure>", self._on_boost_frame_resize)
        ttk.Label(boost_frame, text="add more via Coordinate Tuning tab (name must start with 'boost_')",
                  foreground="#888").pack(anchor="w", pady=(6, 0))

        file_frame = ttk.Frame(parent, padding=(8, 8))
        file_frame.pack(fill="x")
        ttk.Button(file_frame, text="Open config.json", command=self._on_open_config).pack(side="left")
        ttk.Button(file_frame, text="Reload config.json", command=self._on_reload_config).pack(side="left", padx=(6, 0))

        log_frame = ttk.LabelFrame(parent, text="Log", padding=6)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="word")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _field_row(self, parent, label, var, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=var, width=24).grid(row=row, column=1, sticky="w", padx=(6, 0), pady=2)

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
            ttk.Checkbutton(self.boost_checks_frame, text=_humanize_boost_name(name), variable=var,
                             command=lambda n=name: self._on_boost_toggle(n)
                             ).grid(row=i // cols, column=i % cols, sticky="w", padx=(0, 14), pady=1)

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

    def _on_mode_change(self):
        self.config["mode"] = self.mode_var.get()
        self._log(f"mode -> {self.config['mode']}")

    def _on_verbose_change(self):
        self.config["verbose"] = bool(self.verbose_var.get())

    def _on_start(self):
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

    def _on_save_fields(self):
        try:
            state_threshold = float(self.state_threshold_var.get())
            static_threshold = float(self.static_threshold_var.get())
        except ValueError:
            messagebox.showerror("Invalid value", "Thresholds must be numbers.")
            return
        self.config["adb_serial"] = self.adb_serial_var.get().strip()
        self.config["state_match_threshold"] = state_threshold
        self.config["static_threshold"] = static_threshold
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
        self.adb_serial_var.set(self.config["adb_serial"])
        self.state_threshold_var.set(str(self.config["state_match_threshold"]))
        self.static_threshold_var.set(str(self.config["static_threshold"]))
        self._rebuild_boost_checkboxes()
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
