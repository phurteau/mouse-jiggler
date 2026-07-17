"""Zen Mouse Jiggler.

A modern, scheduled keep-awake utility that keeps you shown as active
(e.g. Microsoft Teams "Available") and stops your screen from sleeping --
without ever visibly moving the mouse cursor.

Design goals
------------
* Zen mode: activity is signalled with an invisible F15 keypress, so the
  cursor never moves on screen.
* Awake mode: the display is kept awake with the Windows
  SetThreadExecutionState API. We deliberately do NOT use Windows
  "Presentation Mode" (presentationsettings), which is what silences Teams
  notifications in many other jigglers -- so notifications and sounds keep
  working normally.
* Schedule aware: only active inside a daily start/end window, and it
  auto-resumes the next day on its own.
* User override: while you are actually using the mouse/keyboard, the app
  stays out of the way. After a period of inactivity (default 60s) it
  quietly re-activates.
* Portable & OneDrive friendly: config is stored next to the executable and
  transparently falls back to %LOCALAPPDATA% if that write is blocked (as it
  can be inside a syncing OneDrive folder). Single-instance is enforced with a
  named Win32 mutex -- NOT a lock file beside the exe -- which is what lets it
  run happily from within OneDrive.
* System tray: minimize/close to tray, optionally start minimized, and
  optionally launch at Windows sign-in.
"""

import os
import sys
import json
import time
import queue
import threading
import tkinter as tk
from tkinter import font
from datetime import datetime, timedelta

IS_WINDOWS = sys.platform.startswith("win")
APP_NAME = "ZenMouseJiggler"
APP_TITLE = "Zen Mouse Jiggler"
CONFIG_FILENAME = "zen-jiggler-config.json"

# Optional system-tray support. Degrades gracefully if unavailable.
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except Exception:  # pragma: no cover
    TRAY_AVAILABLE = False

if IS_WINDOWS:
    import winreg

# ---------------------------------------------------------------------------
# Win32 backend (idle detection, invisible activity, keep-awake, mutex).
# ---------------------------------------------------------------------------
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    _KEYEVENTF_KEYUP = 0x0002
    _VK_F15 = 0x7E  # F15: a real key that does nothing on modern systems.

    _ES_CONTINUOUS = 0x80000000
    _ES_SYSTEM_REQUIRED = 0x00000001
    _ES_DISPLAY_REQUIRED = 0x00000002

    def get_idle_seconds():
        """Seconds since the last real *or injected* user input."""
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not _user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0
        millis = _kernel32.GetTickCount() - lii.dwTime
        if millis < 0:  # GetTickCount wrapped (~49 days uptime)
            return 0.0
        return millis / 1000.0

    def inject_invisible_activity():
        """Signal activity via an F15 keypress. The cursor never moves."""
        _user32.keybd_event(_VK_F15, 0, 0, 0)                 # key down
        _user32.keybd_event(_VK_F15, 0, _KEYEVENTF_KEYUP, 0)  # key up

    def set_keep_awake(enabled):
        """Keep the display + system awake (or release the request)."""
        if enabled:
            _kernel32.SetThreadExecutionState(
                _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
            )
        else:
            _kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
else:  # pragma: no cover - non-Windows fallback so the app still runs/tests.
    def get_idle_seconds():
        return 0.0

    def inject_invisible_activity():
        pass

    def set_keep_awake(enabled):
        pass


# Held for the process lifetime so the OS keeps the single-instance lock.
_INSTANCE_LOCK = None


def acquire_single_instance():
    """Return a lock object, or None if another instance is already running.

    Uses an OS file lock (msvcrt.locking) on a file in %LOCALAPPDATA% -- NOT a
    lock file beside the exe. Two consequences:
      * It works reliably in a frozen (PyInstaller) build, unlike a named-mutex
        approach which mis-detects the owner once frozen.
      * The lock lives outside OneDrive, so the app still runs from a synced
        OneDrive folder (lock files beside the exe are what break other
        portable jigglers there).
    The OS releases the lock automatically when the process exits, so a crash
    never leaves a stale lock behind.
    """
    global _INSTANCE_LOCK
    if not IS_WINDOWS:
        return object()
    try:
        import msvcrt
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        lock_dir = os.path.join(base, APP_NAME)
        os.makedirs(lock_dir, exist_ok=True)
        fh = open(os.path.join(lock_dir, "instance.lock"), "a+")
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fh.close()
            return None
        _INSTANCE_LOCK = fh
        return fh
    except Exception:
        # Never let single-instance infrastructure stop the app from running.
        return object()


# ---------------------------------------------------------------------------
# App paths, config, and Windows startup registration.
# ---------------------------------------------------------------------------
def app_dir():
    """Directory the app lives in (folder of the exe when frozen)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _portable_config_path():
    return os.path.join(app_dir(), CONFIG_FILENAME)


def _fallback_config_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME, CONFIG_FILENAME)


DEFAULT_CONFIG = {
    "start_time": "09:00",
    "end_time": "17:00",
    "idle_threshold": 60,
    "awake_mode": True,
    "theme": "dark",
    "start_minimized": False,
    "launch_at_startup": False,
}


def load_config():
    """Load config, preferring the portable (beside-exe) copy."""
    for path in (_portable_config_path(), _fallback_config_path()):
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                merged = dict(DEFAULT_CONFIG)
                merged.update({k: data[k] for k in DEFAULT_CONFIG if k in data})
                return merged
        except Exception:
            continue
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    """Save config beside the exe; fall back to %LOCALAPPDATA% if blocked.

    Returns the path written, or None on total failure.
    """
    for path in (_portable_config_path(), _fallback_config_path()):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2)
            return path
        except Exception:
            continue
    return None


def _startup_command():
    """Command string to relaunch the app minimized, for the Run key."""
    if getattr(sys, "frozen", False):
        return '"{}" --minimized'.format(os.path.abspath(sys.executable))
    # Script: prefer pythonw.exe so no console window flashes at sign-in.
    exe = sys.executable or "python"
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    launcher = pyw if os.path.isfile(pyw) else exe
    script = os.path.abspath(__file__)
    return '"{}" "{}" --minimized'.format(launcher, script)


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def is_startup_enabled():
    if not IS_WINDOWS:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_startup(enabled):
    """Enable/disable launch at Windows sign-in. Returns True on success."""
    if not IS_WINDOWS:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ,
                                  _startup_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Theme palettes.
# ---------------------------------------------------------------------------
ACCENT = "#00ff00"
ACCENT_HOVER = "#00cc00"
DANGER = "#ff453a"
DANGER_HOVER = "#e03a30"

THEMES = {
    "dark": {
        "bg": "#121212", "card": "#1c1c1e", "text": "#f5f5f7",
        "subtext": "#9aa0a6", "entry_bg": "#2a2a2d", "entry_fg": "#ffffff",
        "entry_border": "#3a3a3d", "toggle_text": "#151515",
        "theme_btn_bg": "#2a2a2d", "theme_btn_fg": "#f5f5f7",
    },
    "light": {
        "bg": "#f2f3f5", "card": "#ffffff", "text": "#1a1a1a",
        "subtext": "#5f6368", "entry_bg": "#ffffff", "entry_fg": "#1a1a1a",
        "entry_border": "#d0d3d7", "toggle_text": "#0a0a0a",
        "theme_btn_bg": "#e6e8eb", "theme_btn_fg": "#1a1a1a",
    },
}

STATUS_COLORS = {
    "stopped": "#8a8a8e", "active": ACCENT,
    "paused": "#4aa3ff", "waiting": "#ffb300",
}


# ---------------------------------------------------------------------------
# Core engine.
# ---------------------------------------------------------------------------
class JigglerEngine:
    def __init__(self):
        self.running = False
        self.start_time = "09:00"
        self.end_time = "17:00"
        self.idle_threshold = 60.0
        self.awake_mode = True
        self.poll_seconds = 3.0
        self.state = "stopped"      # stopped / active / paused / waiting
        self._thread = None
        self._awake_asserted = False

    def configure(self, start, end, idle_threshold, awake_mode):
        self.start_time = start
        self.end_time = end
        self.idle_threshold = idle_threshold
        self.awake_mode = awake_mode

    def _in_window(self, now_time):
        try:
            start = datetime.strptime(self.start_time, "%H:%M").time()
            end = datetime.strptime(self.end_time, "%H:%M").time()
        except ValueError:
            return True
        if start <= end:
            return start <= now_time <= end
        return now_time >= start or now_time <= end

    def next_resume_dt(self, now=None):
        now = now or datetime.now()
        try:
            start = datetime.strptime(self.start_time, "%H:%M").time()
        except ValueError:
            return None
        candidate = now.replace(hour=start.hour, minute=start.minute,
                                second=0, microsecond=0)
        if self._in_window(now.time()):
            return now
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def _assert_awake(self, enabled):
        if enabled != self._awake_asserted:
            set_keep_awake(enabled)
            self._awake_asserted = enabled

    def _interruptible_sleep(self, seconds):
        end = time.monotonic() + seconds
        while self.running and time.monotonic() < end:
            time.sleep(min(0.2, max(0.0, end - time.monotonic())))

    def _run(self):
        try:
            while self.running:
                if self._in_window(datetime.now().time()):
                    self._assert_awake(self.awake_mode)
                    if get_idle_seconds() >= self.idle_threshold:
                        inject_invisible_activity()
                        self.state = "active"
                    else:
                        self.state = "paused"
                else:
                    self._assert_awake(False)
                    self.state = "waiting"
                self._interruptible_sleep(self.poll_seconds)
        finally:
            self._assert_awake(False)
            self.running = False
            self.state = "stopped"


# ---------------------------------------------------------------------------
# System tray (pystray). All callbacks push commands onto a queue that the Tk
# main thread drains, so we never touch Tk from the tray thread.
# ---------------------------------------------------------------------------
class TrayIcon:
    def __init__(self, engine, cmd_queue):
        self.engine = engine
        self.q = cmd_queue
        self.icon = None
        self._thread = None

    @staticmethod
    def _image(running):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        color = (0, 255, 0, 255) if running else (140, 140, 142, 255)
        d.ellipse((10, 10, 54, 54), fill=color)
        return img

    def start(self):
        menu = pystray.Menu(
            pystray.MenuItem("Show", lambda i, it: self.q.put(("show",)),
                             default=True),
            pystray.MenuItem(
                lambda it: "Stop" if self.engine.running else "Start",
                lambda i, it: self.q.put(("toggle",))),
            pystray.MenuItem(
                "Launch at startup",
                lambda i, it: self.q.put(("startup_toggle",)),
                checked=lambda it: is_startup_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda i, it: self.q.put(("quit",))),
        )
        self.icon = pystray.Icon(APP_NAME, self._image(self.engine.running),
                                 APP_TITLE, menu)
        self._thread = threading.Thread(target=self.icon.run, daemon=True)
        self._thread.start()

    def refresh(self, running):
        if self.icon is not None:
            try:
                self.icon.icon = self._image(running)
                self.icon.update_menu()
            except Exception:
                pass

    def stop(self):
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None


# ---------------------------------------------------------------------------
# GUI.
# ---------------------------------------------------------------------------
class JigglerApp:
    def __init__(self, root, engine, config=None, start_minimized=False):
        self.root = root
        self.engine = engine
        self.config = dict(DEFAULT_CONFIG)
        if config:
            self.config.update(config)
        self.theme_name = self.config.get("theme", "dark")
        self._last_running = False
        self._cmd_queue = queue.Queue()
        self.tray = None

        root.title(APP_TITLE)
        root.minsize(440, 640)

        self.f_title = font.Font(family="Segoe UI", size=17, weight="bold")
        self.f_label = font.Font(family="Segoe UI", size=10)
        self.f_entry = font.Font(family="Segoe UI", size=12)
        self.f_button = font.Font(family="Segoe UI", size=13, weight="bold")
        self.f_status = font.Font(family="Segoe UI", size=11, weight="bold")
        self.f_small = font.Font(family="Segoe UI", size=9)
        self.f_dot = font.Font(family="Segoe UI", size=15)

        self._build()
        self._load_into_ui()
        self.apply_theme()

        # Tray + window behaviour.
        if TRAY_AVAILABLE:
            self.tray = TrayIcon(self.engine, self._cmd_queue)
            self.tray.start()
            self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
            self.root.bind("<Unmap>", self._on_unmap)
        else:
            self.root.protocol("WM_DELETE_WINDOW", self._on_quit)

        if start_minimized and TRAY_AVAILABLE:
            self.root.after(10, self.hide_to_tray)
        elif start_minimized:
            self.root.after(10, self.root.iconify)

        self._poll_status()

    # -- layout --------------------------------------------------------------
    def _build(self):
        self.outer = tk.Frame(self.root)
        self.outer.pack(fill=tk.BOTH, expand=True)

        self.header = tk.Frame(self.outer)
        self.header.pack(fill=tk.X, padx=24, pady=(20, 8))
        self.title_lbl = tk.Label(self.header, text=APP_TITLE,
                                  font=self.f_title, anchor="w")
        self.title_lbl.pack(side=tk.LEFT)
        self.theme_btn = tk.Button(self.header, text="", font=self.f_small,
                                   relief="flat", bd=0, cursor="hand2",
                                   command=self.toggle_theme, padx=12, pady=6)
        self.theme_btn.pack(side=tk.RIGHT)

        self.card = tk.Frame(self.outer, bd=0)
        self.card.pack(fill=tk.BOTH, expand=True, padx=24, pady=(4, 12))
        self.card_pad = tk.Frame(self.card)
        self.card_pad.pack(fill=tk.BOTH, expand=True, padx=22, pady=22)

        self.sched_hdr = tk.Label(self.card_pad, text="SCHEDULE",
                                  font=self.f_small, anchor="w")
        self.sched_hdr.pack(fill=tk.X, pady=(0, 6))
        self.time_row = tk.Frame(self.card_pad)
        self.time_row.pack(fill=tk.X, pady=(0, 12))
        self.start_lbl, self.start_entry = self._field(
            self.time_row, "Start time", "09:00", side=tk.LEFT)
        self.end_lbl, self.end_entry = self._field(
            self.time_row, "End time", "17:00", side=tk.RIGHT)

        self.behav_hdr = tk.Label(self.card_pad, text="BEHAVIOUR",
                                  font=self.f_small, anchor="w")
        self.behav_hdr.pack(fill=tk.X, pady=(4, 6))
        self.idle_lbl, self.idle_entry = self._field(
            self.card_pad, "Re-activate after idle (seconds)", "60",
            side=None)

        self.awake_var = tk.BooleanVar(value=True)
        self.awake_chk = self._check(
            "Keep screen awake (won't sleep during schedule)", self.awake_var)
        self.min_var = tk.BooleanVar(value=False)
        self.min_chk = self._check(
            "Start minimized to tray", self.min_var,
            command=self._save_ui_config)
        self.startup_var = tk.BooleanVar(value=False)
        self.startup_chk = self._check(
            "Launch at Windows startup", self.startup_var,
            command=self._on_startup_checkbox)

        self.zen_note = tk.Label(
            self.card_pad, justify="left", anchor="w", font=self.f_small,
            text=("Zen mode: the cursor never moves. Teams stays "
                  "\u201cAvailable\u201d and your\nnotifications & sounds "
                  "are never muted."))
        self.zen_note.pack(fill=tk.X, pady=(8, 14))

        self.toggle_btn = tk.Button(self.card_pad, text="Start",
                                    font=self.f_button, relief="flat", bd=0,
                                    cursor="hand2", command=self.toggle,
                                    activeforeground="#000000")
        self.toggle_btn.pack(fill=tk.X, ipady=10, pady=(2, 8))

        self.status_row = tk.Frame(self.card_pad)
        self.status_row.pack(fill=tk.X, pady=(6, 0))
        self.status_dot = tk.Label(self.status_row, text="\u25cf",
                                   font=self.f_dot)
        self.status_dot.pack(side=tk.LEFT, padx=(0, 8))
        self.status_txt = tk.Label(self.status_row, text="Stopped",
                                   font=self.f_status, anchor="w")
        self.status_txt.pack(side=tk.LEFT)

        self.footer = tk.Label(self.outer, font=self.f_small, anchor="w",
                               justify="left")
        self.footer.pack(fill=tk.X, padx=24, pady=(0, 16))

        self._entries = [self.start_entry, self.end_entry, self.idle_entry]
        self._section_hdrs = [self.sched_hdr, self.behav_hdr]
        self._field_labels = [self.start_lbl, self.end_lbl, self.idle_lbl]
        self._checks = [self.awake_chk, self.min_chk, self.startup_chk]

    def _field(self, parent, label, default, side=tk.LEFT):
        wrap = tk.Frame(parent)
        if side is None:
            wrap.pack(fill=tk.X)
        else:
            wrap.pack(side=side, fill=tk.X, expand=True,
                      padx=(0, 6) if side == tk.LEFT else (6, 0))
        lbl = tk.Label(wrap, text=label, font=self.f_label, anchor="w")
        lbl.pack(fill=tk.X, pady=(0, 3))
        entry = tk.Entry(wrap, font=self.f_entry, justify="center",
                         relief="flat", bd=8)
        entry.insert(0, default)
        entry.pack(fill=tk.X)
        return lbl, entry

    def _check(self, text, var, command=None):
        chk = tk.Checkbutton(self.card_pad, text=text, variable=var,
                             font=self.f_label, anchor="w", bd=0,
                             highlightthickness=0, cursor="hand2",
                             command=command)
        chk.pack(fill=tk.X, pady=(6, 0))
        return chk

    # -- config <-> ui -------------------------------------------------------
    def _load_into_ui(self):
        c = self.config
        for entry, key in ((self.start_entry, "start_time"),
                           (self.end_entry, "end_time")):
            entry.delete(0, tk.END)
            entry.insert(0, str(c.get(key, "")))
        self.idle_entry.delete(0, tk.END)
        self.idle_entry.insert(0, str(c.get("idle_threshold", 60)))
        self.awake_var.set(bool(c.get("awake_mode", True)))
        self.min_var.set(bool(c.get("start_minimized", False)))
        # Reflect the real registry state, not just stored config.
        self.startup_var.set(is_startup_enabled())

    def _collect_config(self):
        cfg = dict(self.config)
        cfg.update({
            "start_time": self.start_entry.get().strip(),
            "end_time": self.end_entry.get().strip(),
            "theme": self.theme_name,
            "awake_mode": bool(self.awake_var.get()),
            "start_minimized": bool(self.min_var.get()),
            "launch_at_startup": bool(self.startup_var.get()),
        })
        try:
            cfg["idle_threshold"] = float(self.idle_entry.get())
        except ValueError:
            pass
        return cfg

    def _save_ui_config(self):
        self.config = self._collect_config()
        save_config(self.config)

    def _on_startup_checkbox(self):
        ok = set_startup(bool(self.startup_var.get()))
        if not ok:
            self.startup_var.set(is_startup_enabled())
            self._flash_footer("Couldn't change the startup setting.")
        self._save_ui_config()

    # -- theming -------------------------------------------------------------
    def apply_theme(self):
        t = THEMES[self.theme_name]
        self.root.config(bg=t["bg"])
        for w in (self.outer, self.header, self.status_row):
            w.config(bg=t["bg"])
        self.footer.config(bg=t["bg"], fg=t["subtext"])
        self.title_lbl.config(bg=t["bg"], fg=t["text"])
        self.theme_btn.config(bg=t["theme_btn_bg"], fg=t["theme_btn_fg"],
                              activebackground=t["theme_btn_bg"],
                              activeforeground=t["theme_btn_fg"],
                              text="\u2600  Light" if self.theme_name == "dark"
                              else "\u263e  Dark")
        for w in (self.card, self.card_pad, self.time_row):
            w.config(bg=t["card"])
        for hdr in self._section_hdrs:
            hdr.config(bg=t["card"], fg=t["subtext"])
        for lbl in self._field_labels:
            lbl.config(bg=t["card"], fg=t["subtext"])
        for e in self._entries:
            e.config(bg=t["entry_bg"], fg=t["entry_fg"],
                     insertbackground=t["entry_fg"],
                     disabledbackground=t["entry_bg"],
                     highlightthickness=1, highlightbackground=t["entry_border"],
                     highlightcolor=ACCENT)
            e.master.config(bg=t["card"])
        for chk in self._checks:
            chk.config(bg=t["card"], fg=t["text"], activebackground=t["card"],
                       activeforeground=t["text"], selectcolor=t["entry_bg"])
        self.zen_note.config(bg=t["card"], fg=t["subtext"])
        self.status_dot.config(bg=t["card"])
        self.status_txt.config(bg=t["card"], fg=t["text"])
        self._refresh_toggle_visual()
        self._refresh_status()

    def toggle_theme(self):
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.apply_theme()
        self._save_ui_config()

    def _refresh_toggle_visual(self):
        t = THEMES[self.theme_name]
        if self.engine.running:
            self.toggle_btn.config(text="Stop", bg=DANGER, fg="#ffffff",
                                   activebackground=DANGER_HOVER,
                                   activeforeground="#ffffff")
        else:
            self.toggle_btn.config(text="Start", bg=ACCENT,
                                   fg=t["toggle_text"],
                                   activebackground=ACCENT_HOVER,
                                   activeforeground=t["toggle_text"])

    # -- actions -------------------------------------------------------------
    def _validate_time(self, value):
        try:
            datetime.strptime(value, "%H:%M")
            return True
        except ValueError:
            return False

    def toggle(self):
        if self.engine.running:
            self.engine.stop()
            self._refresh_toggle_visual()
            return
        start = self.start_entry.get().strip()
        end = self.end_entry.get().strip()
        if not self._validate_time(start) or not self._validate_time(end):
            self._flash_footer("Times must be in HH:MM 24-hour format.")
            return
        try:
            idle_threshold = float(self.idle_entry.get())
        except ValueError:
            self._flash_footer("Idle seconds must be a number.")
            return
        if idle_threshold < 5:
            self._flash_footer("Idle seconds must be at least 5.")
            return
        self.engine.configure(start, end, idle_threshold,
                              bool(self.awake_var.get()))
        self._save_ui_config()
        self._set_inputs_state("disabled")
        self.engine.start()
        self._refresh_toggle_visual()

    def _set_inputs_state(self, state):
        for e in self._entries:
            e.config(state=state)
        for chk in self._checks:
            chk.config(state=state)

    # -- tray / window -------------------------------------------------------
    def hide_to_tray(self):
        if TRAY_AVAILABLE:
            self.root.withdraw()
        else:
            self.root.iconify()

    def show_window(self):
        self.root.deiconify()
        self.root.after(0, self.root.lift)
        try:
            self.root.focus_force()
        except tk.TclError:
            pass

    def _on_unmap(self, event):
        # Fired when the window is minimized; route to the tray instead.
        if event.widget is self.root and TRAY_AVAILABLE:
            if self.root.state() == "iconic":
                self.root.after(1, self.hide_to_tray)

    # -- status --------------------------------------------------------------
    def _refresh_status(self):
        state = self.engine.state
        self.status_dot.config(fg=STATUS_COLORS.get(state, "#8a8a8e"))
        if state == "active":
            self.status_txt.config(text="Active \u2014 keeping you awake (zen)")
        elif state == "paused":
            self.status_txt.config(text="Standing by \u2014 you're active")
        elif state == "waiting":
            resume = self.engine.next_resume_dt()
            when = resume.strftime("%a %H:%M") if resume else self.engine.start_time
            self.status_txt.config(text=f"Waiting \u2014 resumes {when}")
        else:
            self.status_txt.config(text="Stopped")
        self._refresh_footer()

    def _refresh_footer(self):
        if self.engine.running:
            awake = "on" if self.engine.awake_mode else "off"
            self.footer.config(
                text=(f"Schedule {self.engine.start_time}\u2013"
                      f"{self.engine.end_time} \u00b7 auto-resumes daily \u00b7 "
                      f"screen-awake {awake}"))
        else:
            self.footer.config(
                text="Runs on your daily schedule and resumes automatically "
                     "each day.")

    def _flash_footer(self, msg):
        self.footer.config(text=msg)

    def _drain_commands(self):
        try:
            while True:
                cmd = self._cmd_queue.get_nowait()[0]
                if cmd == "show":
                    self.show_window()
                elif cmd == "toggle":
                    self.toggle()
                elif cmd == "startup_toggle":
                    self.startup_var.set(not self.startup_var.get())
                    self._on_startup_checkbox()
                elif cmd == "quit":
                    self._on_quit()
                    return
        except queue.Empty:
            pass

    def _poll_status(self):
        # Main-thread pump: drain tray commands, react to engine state.
        self._drain_commands()
        if self._last_running != self.engine.running:
            if not self.engine.running:
                self._set_inputs_state("normal")
            self._refresh_toggle_visual()
            if self.tray:
                self.tray.refresh(self.engine.running)
        self._last_running = self.engine.running
        self._refresh_status()
        self.root.after(300, self._poll_status)

    def _on_quit(self):
        self.engine.stop()
        set_keep_awake(False)
        if self.tray:
            self.tray.stop()
        self.root.destroy()


def _parse_args(argv):
    flags = {a.lstrip("-/").lower() for a in argv[1:]}
    return "minimized" in flags or "min" in flags


def main():
    # Single-instance guard (OS file lock; safe inside OneDrive).
    guard = acquire_single_instance()
    if guard is None:
        return

    minimized_arg = _parse_args(sys.argv)
    config = load_config()
    start_minimized = minimized_arg or bool(config.get("start_minimized"))

    engine = JigglerEngine()
    root = tk.Tk()
    JigglerApp(root, engine, config=config, start_minimized=start_minimized)
    root.mainloop()


if __name__ == "__main__":
    main()
