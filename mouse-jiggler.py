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
import math
import queue
import colorsys
import threading
import webbrowser
import zipfile
import tempfile
import subprocess
import urllib.request
import tkinter as tk
from tkinter import font
from datetime import datetime, timedelta

IS_WINDOWS = sys.platform.startswith("win")
APP_NAME = "ZenMouseJiggler"
APP_TITLE = "Zen Mouse Jiggler"
APP_VERSION = "2.1.2"
CONFIG_FILENAME = "zen-jiggler-config.json"
DEFAULT_ACCENT = "#025500"   # dimmed green; drives all accent highlights

# Auto-update: checked against the GitHub Releases API on launch.
GITHUB_REPO = "phurteau/mouse-jiggler"
LATEST_RELEASE_API = "https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO
AUTO_UPDATE_CHECK = True

# Optional system-tray support. Degrades gracefully if unavailable.
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except Exception:  # pragma: no cover
    TRAY_AVAILABLE = False

# Pillow (independent of pystray) for rendering the app/tray/window icon.
try:
    from PIL import Image as _IconImage, ImageDraw as _IconDraw
    ICON_AVAILABLE = True
except Exception:  # pragma: no cover
    ICON_AVAILABLE = False

# Optional Pillow-Tk bridge for the HSV colour wheel. Falls back to a hex-only
# picker if unavailable.
try:
    from PIL import Image as _WheelImage, ImageTk as _WheelImageTk
    WHEEL_AVAILABLE = True
except Exception:  # pragma: no cover
    WHEEL_AVAILABLE = False

if IS_WINDOWS:
    import winreg


def render_icon(size=64, running=True):
    """Render the branded app icon (dark tile + green mouse cursor + jiggle
    arcs) at `size` px. When `running` is False it is drawn in neutral gray
    (used by the tray to show the stopped state). Returns a Pillow image, or
    None if Pillow is unavailable."""
    if not ICON_AVAILABLE:
        return None
    accent = (3, 178, 0, 255) if running else (150, 150, 152, 255)
    accent_dk = (2, 85, 0, 255) if running else (110, 110, 112, 255)
    tile = (14, 14, 16, 255)
    border = (42, 42, 46, 255)
    white = (240, 240, 240, 255)

    img = _IconImage.new("RGBA", (size, size), (0, 0, 0, 0))
    d = _IconDraw.Draw(img)
    s = size / 512.0
    m = int(24 * s)
    d.rounded_rectangle([m, m, size - m, size - m], radius=int(96 * s),
                        fill=tile, outline=border, width=max(1, int(6 * s)))
    cx, cy = size * 0.60, size * 0.46
    for i, r in enumerate((0.16, 0.24, 0.32)):
        col = accent if i == 0 else accent_dk
        rr = int(size * r)
        d.arc([cx - rr, cy - rr, cx + rr, cy + rr], -55, 55, fill=col,
              width=max(2, int((14 - i * 3) * s)))
    scale = size * 0.30
    ox, oy = size * 0.30, size * 0.26
    pts = [(0.0, 0.0), (0.0, 0.72), (0.20, 0.55), (0.33, 0.86),
           (0.46, 0.80), (0.32, 0.50), (0.56, 0.50)]
    poly = [(ox + x * scale, oy + y * scale) for x, y in pts]
    d.polygon(poly, fill=accent, outline=white)
    d.line(poly + [poly[0]], fill=white, width=max(2, int(scale * 0.03)),
           joint="curve")
    return img

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


def release_single_instance():
    """Explicitly release the single-instance lock (the OS also does this on
    process exit, but releasing at graceful shutdown frees it immediately)."""
    global _INSTANCE_LOCK
    fh = _INSTANCE_LOCK
    if fh is None or not IS_WINDOWS:
        _INSTANCE_LOCK = None
        return
    try:
        import msvcrt
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        fh.close()
    except Exception:
        pass
    finally:
        _INSTANCE_LOCK = None


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
    "work_schedule_enabled": False,
    "work_days": [1, 2, 3, 4, 5],   # Sun=0..Sat=6; default Mon-Fri
    "theme": "dark",
    "accent": DEFAULT_ACCENT,
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


def reconcile_startup_entry():
    """Self-heal the startup Run key. If it exists but points at a stale path
    (e.g. the portable folder was moved or renamed), rewrite it to the current
    executable location so Launch-at-startup keeps working. No-op when the key
    is absent or already correct."""
    if not IS_WINDOWS:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            try:
                current, _ = winreg.QueryValueEx(key, APP_NAME)
            except FileNotFoundError:
                return  # not enabled -> nothing to heal
    except OSError:
        return
    expected = _startup_command()
    if str(current).strip() != expected.strip():
        set_startup(True)


# ---------------------------------------------------------------------------
# Update checking (GitHub Releases).
# ---------------------------------------------------------------------------
def parse_version(tag):
    """Turn a tag like 'v2.1.0' into a comparable (2, 1, 0) tuple."""
    if not tag:
        return (0, 0, 0)
    text = str(tag).strip().lstrip("vV")
    parts = []
    for chunk in text.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(latest, current):
    return parse_version(latest) > parse_version(current)


def check_for_update(current_version=APP_VERSION, timeout=6):
    """Return update info dict if a newer release exists, else None.

    Never raises -- any network/parse failure just returns None so the app
    behaves normally offline.
    """
    try:
        req = urllib.request.Request(
            LATEST_RELEASE_API,
            headers={"User-Agent": APP_NAME,
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        tag = data.get("tag_name", "")
        if not is_newer(tag, current_version):
            return None
        download_url = None
        asset_name = None
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if name.lower().endswith(".zip"):
                download_url = asset.get("browser_download_url")
                asset_name = name
                break
        return {
            "version": tag,
            "html_url": data.get("html_url")
            or "https://github.com/%s/releases/latest" % GITHUB_REPO,
            "download_url": download_url,
            "asset_name": asset_name,
        }
    except Exception:
        return None


def download_file(url, dest_path, progress_cb=None, timeout=30):
    """Stream a URL to dest_path, calling progress_cb(fraction) as it goes."""
    req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        got = 0
        with open(dest_path, "wb") as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                if progress_cb and total:
                    progress_cb(got / total)
    if progress_cb:
        progress_cb(1.0)
    return dest_path


def downloads_dir():
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    return d if os.path.isdir(d) else os.path.expanduser("~")


def stage_update(zip_path, exe_name="ZenMouseJiggler.exe"):
    """Extract the update zip to a temp folder and return the folder that
    directly contains `exe_name` (the new app files)."""
    staging = tempfile.mkdtemp(prefix="zmj_update_")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(staging)
    for root_dir, _dirs, files in os.walk(staging):
        if exe_name in files:
            return staging, root_dir
    raise RuntimeError("Update package did not contain %s" % exe_name)


def _updater_script(src_dir, install_dir, staging_dir, exe_name):
    """Batch that waits for the app to close, swaps in the new files (keeping
    the user's config), relaunches, then deletes itself. Uses only signed
    Windows tools (cmd/robocopy), so it runs even under Application Control."""
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        "set /a TRIES=0\r\n"
        ":waitloop\r\n"
        "tasklist /FI \"IMAGENAME eq {exe}\" 2>nul | find /I \"{exe}\" >nul\r\n"
        "if not errorlevel 1 (\r\n"
        "  set /a TRIES+=1\r\n"
        "  if %TRIES% GEQ 60 goto docopy\r\n"
        "  timeout /t 1 /nobreak >nul\r\n"
        "  goto waitloop\r\n"
        ")\r\n"
        ":docopy\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        "robocopy \"{src}\" \"{dst}\" /E /R:10 /W:1 /NFL /NDL /NJH /NJS /NP >nul\r\n"
        "start \"\" \"{dst}\\{exe}\"\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        "rmdir /s /q \"{staging}\" >nul 2>&1\r\n"
        "(goto) 2>nul & del \"%~f0\"\r\n"
    ).format(exe=exe_name, src=src_dir, dst=install_dir, staging=staging_dir)


def apply_update(zip_path, install_dir, exe_name="ZenMouseJiggler.exe"):
    """Stage the downloaded update and launch a detached helper that installs
    it after this process exits. The caller should quit right after this."""
    staging, src_dir = stage_update(zip_path, exe_name)
    script = _updater_script(src_dir, install_dir, staging, exe_name)
    helper = os.path.join(tempfile.gettempdir(),
                          "zmj_update_%d.cmd" % os.getpid())
    with open(helper, "w", encoding="ascii") as fh:
        fh.write(script)
    # Detached, no console window, survives our exit.
    flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED|NEW_GROUP|NO_WINDOW
    subprocess.Popen(["cmd", "/c", helper], creationflags=flags, close_fds=True)
    return True


# ---------------------------------------------------------------------------
# Design tokens + colour helpers.
#
# A single user-chosen ACCENT drives all highlights. Everything else is
# neutral. Dark is true-black with neutral-gray panels. These dicts mirror the
# CSS custom-property token set (bg/bg2/panel/panel2/line/txt/dim/glow); the
# accent-derived tokens (acc/acc2/ink) are computed per-render from the accent.
# ---------------------------------------------------------------------------
DANGER = "#ff453a"
DANGER_HOVER = "#e03a30"


def _hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(
        *(max(0, min(255, int(round(c)))) for c in rgb))


def normalize_hex(value, fallback=DEFAULT_ACCENT):
    """Return a validated #rrggbb string, or `fallback` if unparseable."""
    if not isinstance(value, str):
        return fallback
    v = value.strip()
    if not v.startswith("#"):
        v = "#" + v
    if len(v) == 4:  # short form #abc -> #aabbcc
        v = "#" + "".join(ch * 2 for ch in v[1:])
    try:
        _hex_to_rgb(v)
    except (ValueError, IndexError):
        return fallback
    return v.lower()


def darken(hex_color, factor=0.8):
    r, g, b = _hex_to_rgb(hex_color)
    return _rgb_to_hex((r * factor, g * factor, b * factor))


def derive_acc2(hex_color):
    """Brighter companion of the accent: same hue, saturation >= 45%,
    lightness +20% (capped ~75%). Used for hovers, glows, focus, spinner."""
    r, g, b = (c / 255 for c in _hex_to_rgb(hex_color))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    s = max(s, 0.45)
    l = min(0.75, l + 0.20)
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return _rgb_to_hex((r * 255, g * 255, b * 255))


def text_on(hex_color):
    """Ink colour that sits ON an accent fill: dark ink for light accents,
    white for dark ones (YIQ luminance threshold ~140)."""
    r, g, b = _hex_to_rgb(hex_color)
    yiq = (r * 299 + g * 587 + b * 114) / 1000
    return "#08140a" if yiq > 140 else "#ffffff"


def hsv_to_hex(h, s, v):
    """h, s, v in 0..1 -> #rrggbb."""
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)),
                                  max(0.0, min(1.0, v)))
    return _rgb_to_hex((r * 255, g * 255, b * 255))


def hex_to_hsv(hex_color):
    """#rrggbb -> (h, s, v) each in 0..1."""
    r, g, b = (c / 255 for c in _hex_to_rgb(hex_color))
    return colorsys.rgb_to_hsv(r, g, b)


# Token sets per theme. `acc`, `acc2` and `ink` are injected per-render.
THEMES = {
    # DARK (default): true black, neutral-gray panels, no colour tint.
    "dark": {
        "bg": "#000000", "bg2": "#060606", "panel": "#101012",
        "panel2": "#17171a", "line": "#2a2a2e",
        "txt": "#ededed", "dim": "#9a9a9a",
    },
    # LIGHT: soft off-white.
    "light": {
        "bg": "#eef4ef", "bg2": "#e6ede8", "panel": "#ffffff",
        "panel2": "#f2f7f3", "line": "#cfe0d4",
        "txt": "#12251a", "dim": "#5c7a66",
    },
}


def resolve_tokens(theme_name, accent):
    """Merge a theme's neutral tokens with the accent-derived ones."""
    tok = dict(THEMES.get(theme_name, THEMES["dark"]))
    tok["acc"] = accent
    tok["acc2"] = derive_acc2(accent)
    tok["ink"] = text_on(accent)
    return tok


STATUS_COLORS = {
    "stopped": "#8a8a8e", "active": DEFAULT_ACCENT,
    "paused": "#4aa3ff", "waiting": "#ffb300",
}


_WHEEL_CACHE = {}


def _render_wheel(size, value):
    """Render an HSV hue/saturation wheel (hue = angle, saturation = radius)
    at the given brightness (value) as a Pillow RGBA image."""
    radius = size / 2.0
    buf = bytearray(size * size * 4)
    atan2, degrees, hsv = math.atan2, math.degrees, colorsys.hsv_to_rgb
    r2 = radius * radius
    for y in range(size):
        dy = y - radius
        for x in range(size):
            dx = x - radius
            d2 = dx * dx + dy * dy
            i = (y * size + x) * 4
            if d2 <= r2:
                dist = d2 ** 0.5
                h = (degrees(atan2(dy, dx)) % 360) / 360.0
                s = dist / radius if dist < radius else 1.0
                r, g, b = hsv(h, s, value)
                buf[i] = int(r * 255)
                buf[i + 1] = int(g * 255)
                buf[i + 2] = int(b * 255)
                buf[i + 3] = 255
    return _WheelImage.frombytes("RGBA", (size, size), bytes(buf))


def wheel_image(size, value):
    key = (size, round(value, 2))
    img = _WHEEL_CACHE.get(key)
    if img is None:
        img = _render_wheel(size, value)
        _WHEEL_CACHE[key] = img
    return img


def wheel_pos_to_hs(x, y, size):
    """Canvas (x, y) -> (hue, saturation), both 0..1."""
    radius = size / 2.0
    dx, dy = x - radius, y - radius
    dist = (dx * dx + dy * dy) ** 0.5
    h = (math.degrees(math.atan2(dy, dx)) % 360) / 360.0
    s = min(1.0, dist / radius) if radius else 0.0
    return h, s


def wheel_hs_to_pos(h, s, size):
    """(hue, saturation) -> canvas (x, y)."""
    radius = size / 2.0
    ang = h * 2 * math.pi
    return radius + math.cos(ang) * s * radius, radius + math.sin(ang) * s * radius


# ---------------------------------------------------------------------------
# Core engine.
# ---------------------------------------------------------------------------
class JigglerEngine:
    # Day indices: Sunday = 0 ... Saturday = 6 (week starts Sunday).
    def __init__(self):
        self.running = False
        self.start_time = "09:00"
        self.end_time = "17:00"
        self.idle_threshold = 60.0
        self.awake_mode = True
        self.work_schedule_enabled = False
        self.work_days = set(range(7))   # all days by default
        self.poll_seconds = 3.0
        self.state = "stopped"      # stopped / active / paused / waiting
        self._thread = None
        self._awake_asserted = False

    def configure(self, start, end, idle_threshold, awake_mode,
                  work_schedule_enabled=False, work_days=None):
        self.start_time = start
        self.end_time = end
        self.idle_threshold = idle_threshold
        self.awake_mode = awake_mode
        self.work_schedule_enabled = bool(work_schedule_enabled)
        self.work_days = (set(work_days) if work_days is not None
                          else set(range(7)))

    @staticmethod
    def day_index(dt):
        """Sunday = 0 ... Saturday = 6 for a datetime/date."""
        return (dt.weekday() + 1) % 7

    def is_active_day(self, dt):
        if not self.work_schedule_enabled:
            return True
        return self.day_index(dt) in self.work_days

    def _active_now(self, now):
        return self.is_active_day(now) and self._in_window(now.time())

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
        if self._active_now(now):
            return now
        # Scan forward for the next active day whose window is still ahead.
        for offset in range(0, 9):
            day = (now + timedelta(days=offset)).date()
            cand = datetime.combine(day, start)
            if self.work_schedule_enabled and self.day_index(cand) not in self.work_days:
                continue
            if offset == 0 and cand <= now:
                continue  # today's window already passed
            return cand
        return None

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
                if self._active_now(datetime.now()):
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
        img = render_icon(64, running)
        if img is not None:
            return img
        # Fallback: simple status dot if the renderer is unavailable.
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        color = (3, 178, 0, 255) if running else (140, 140, 142, 255)
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
# Accent picker: an HSV colour wheel (hue = angle, saturation = radius) with a
# brightness slider, hex input, live preview and reset.
# ---------------------------------------------------------------------------
class AccentWheelDialog(tk.Toplevel):
    SIZE = 200

    def __init__(self, parent, accent, tokens, on_change, on_commit=None):
        super().__init__(parent)
        self.title("Accent color")
        self.resizable(False, False)
        self.tok = tokens
        self.on_change = on_change
        self.on_commit = on_commit
        self._imgtk = None
        self._editing_hex = False
        h, s, v = hex_to_hsv(normalize_hex(accent))
        self.h, self.s, self.v = h, s, v

        self._build()
        try:
            self.transient(parent)
            self.grab_set()
        except tk.TclError:
            pass
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._repaint_wheel()
        self._sync_widgets(apply=False)

    def _build(self):
        t = self.tok
        self.configure(bg=t["panel"])
        pad = 16

        self.canvas = tk.Canvas(self, width=self.SIZE, height=self.SIZE,
                                highlightthickness=0, bd=0, bg=t["panel"],
                                cursor="crosshair")
        self.canvas.pack(padx=pad, pady=(pad, 10))
        self.canvas.bind("<Button-1>", self._on_wheel)
        self.canvas.bind("<B1-Motion>", self._on_wheel)

        brow = tk.Frame(self, bg=t["panel"])
        brow.pack(fill=tk.X, padx=pad)
        tk.Label(brow, text="Brightness", bg=t["panel"], fg=t["dim"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.val = tk.IntVar(value=int(round(self.v * 100)))
        self.slider = tk.Scale(brow, from_=0, to=100, orient=tk.HORIZONTAL,
                               variable=self.val, command=self._on_slider,
                               showvalue=False, bg=t["panel"], fg=t["txt"],
                               troughcolor=t["panel2"], highlightthickness=0,
                               bd=0, sliderrelief="flat", length=150,
                               activebackground=t["acc2"])
        self.slider.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(10, 0))

        hrow = tk.Frame(self, bg=t["panel"])
        hrow.pack(fill=tk.X, padx=pad, pady=(12, 0))
        self.preview = tk.Label(hrow, width=3, bg=t["acc"], relief="flat",
                                highlightthickness=1, highlightbackground=t["line"])
        self.preview.pack(side=tk.LEFT, ipady=8)
        self.hex_var = tk.StringVar()
        self.hex_entry = tk.Entry(hrow, textvariable=self.hex_var, width=10,
                                  justify="center", font=("Segoe UI", 11),
                                  relief="flat", bd=6, bg=t["panel2"], fg=t["txt"],
                                  insertbackground=t["txt"], highlightthickness=1,
                                  highlightbackground=t["line"],
                                  highlightcolor=t["acc2"])
        self.hex_entry.pack(side=tk.LEFT, padx=(10, 0))
        self.hex_entry.bind("<FocusIn>",
                            lambda e: setattr(self, "_editing_hex", True))
        self.hex_entry.bind("<Return>", self._on_hex)
        self.hex_entry.bind("<FocusOut>", self._on_hex)

        frow = tk.Frame(self, bg=t["panel"])
        frow.pack(fill=tk.X, padx=pad, pady=(14, pad))
        self.reset_btn = tk.Button(frow, text="Reset to default", font=("Segoe UI", 9),
                                   relief="flat", bd=0, cursor="hand2",
                                   command=self._on_reset, padx=12, pady=5,
                                   bg=t["panel2"], fg=t["txt"],
                                   activebackground=t["panel2"], activeforeground=t["txt"],
                                   highlightthickness=1, highlightbackground=t["line"])
        self.reset_btn.pack(side=tk.LEFT)
        self.done_btn = tk.Button(frow, text="Done", font=("Segoe UI", 9, "bold"),
                                  relief="flat", bd=0, cursor="hand2",
                                  command=self._close, padx=18, pady=5,
                                  bg=t["acc"], fg=t["ink"],
                                  activebackground=t["acc2"], activeforeground=t["ink"])
        self.done_btn.pack(side=tk.RIGHT)

    # -- rendering -----------------------------------------------------------
    def _repaint_wheel(self):
        img = wheel_image(self.SIZE, self.v)
        self._imgtk = _WheelImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._imgtk)
        self._draw_dot()

    def _draw_dot(self):
        x, y = wheel_hs_to_pos(self.h, self.s, self.SIZE)
        r = 7
        self.canvas.delete("dot")
        self.canvas.create_oval(x - r - 1, y - r - 1, x + r + 1, y + r + 1,
                                outline="#000000", width=1, tags="dot")
        self.canvas.create_oval(x - r, y - r, x + r, y + r,
                                outline="#ffffff", width=2, tags="dot")

    def _current_hex(self):
        return hsv_to_hex(self.h, self.s, self.v)

    def _sync_widgets(self, apply=True):
        hexv = self._current_hex()
        self.preview.config(bg=hexv)
        if not self._editing_hex:
            self.hex_var.set(hexv.upper())
        self.done_btn.config(bg=hexv, fg=text_on(hexv),
                             activebackground=derive_acc2(hexv),
                             activeforeground=text_on(hexv))
        self._draw_dot()
        if apply and self.on_change:
            self.on_change(hexv)

    # -- interaction ---------------------------------------------------------
    def _on_wheel(self, event):
        self.h, self.s = wheel_pos_to_hs(event.x, event.y, self.SIZE)
        self._sync_widgets()

    def _on_slider(self, _value):
        self.v = self.val.get() / 100.0
        self._repaint_wheel()
        self._sync_widgets()

    def _on_hex(self, _event=None):
        raw = self.hex_var.get().strip()
        norm = normalize_hex(raw, fallback=None)
        if norm:
            self.h, self.s, self.v = hex_to_hsv(norm)
            self.val.set(int(round(self.v * 100)))
            self._editing_hex = False
            self._repaint_wheel()
            self._sync_widgets()
        else:
            self._editing_hex = False
            self.hex_var.set(self._current_hex().upper())

    def _on_reset(self):
        self.h, self.s, self.v = hex_to_hsv(DEFAULT_ACCENT)
        self.val.set(int(round(self.v * 100)))
        self._repaint_wheel()
        self._sync_widgets()

    def _close(self):
        if self.on_commit:
            self.on_commit(self._current_hex())
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


# ---------------------------------------------------------------------------
# GUI.
# ---------------------------------------------------------------------------
class JigglerApp:
    def __init__(self, root, engine, config=None, start_minimized=False,
                 launched_at_startup=False):
        self.root = root
        self.engine = engine
        self.config = dict(DEFAULT_CONFIG)
        if config:
            self.config.update(config)
        self.theme_name = self.config.get("theme", "dark")
        self.accent = normalize_hex(self.config.get("accent", DEFAULT_ACCENT))
        self._tok = resolve_tokens(self.theme_name, self.accent)
        self._last_running = False
        self._cmd_queue = queue.Queue()
        self.tray = None
        self._update_info = None
        self._downloaded_path = None

        root.title(APP_TITLE)
        root.minsize(460, 760)
        self._set_window_icon()

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

        # Auto-start jiggling when launched at Windows startup, so the saved
        # schedule resumes with no clicks. (Manual launches stay stopped.)
        if launched_at_startup:
            self.root.after(50, self._auto_start)

        if AUTO_UPDATE_CHECK:
            threading.Thread(target=self._bg_check_update, daemon=True).start()

        self._poll_status()

    # -- layout --------------------------------------------------------------
    def _build(self):
        self.outer = tk.Frame(self.root)
        self.outer.pack(fill=tk.BOTH, expand=True)

        # Update banner (hidden until an update is found).
        self.banner = tk.Frame(self.outer)
        self.banner_lbl = tk.Label(self.banner, font=self.f_small, anchor="w",
                                   justify="left")
        self.banner_lbl.pack(side=tk.LEFT, padx=(14, 8), pady=8, fill=tk.X,
                             expand=True)
        self.banner_dismiss = tk.Button(self.banner, text="\u2715",
                                        font=self.f_small, relief="flat", bd=0,
                                        cursor="hand2", command=self._hide_banner,
                                        padx=8, pady=2)
        self.banner_dismiss.pack(side=tk.RIGHT, padx=(0, 10))
        self.banner_action = tk.Button(self.banner, text="Download",
                                       font=self.f_small, relief="flat", bd=0,
                                       cursor="hand2",
                                       command=self._download_update,
                                       padx=14, pady=4)
        self.banner_action.pack(side=tk.RIGHT, padx=(0, 8))

        self.header = tk.Frame(self.outer)
        self.header.pack(fill=tk.X, padx=24, pady=(20, 8))
        self.title_lbl = tk.Label(self.header, text=APP_TITLE,
                                  font=self.f_title, anchor="w")
        self.title_lbl.pack(side=tk.LEFT)
        self.theme_btn = tk.Button(self.header, text="", font=self.f_small,
                                   relief="flat", bd=0, cursor="hand2",
                                   command=self.toggle_theme, padx=12, pady=6)
        self.theme_btn.pack(side=tk.RIGHT)
        self.quit_btn = tk.Button(self.header, text="Quit", font=self.f_small,
                                  relief="flat", bd=0, cursor="hand2",
                                  command=self._on_quit, padx=12, pady=6)
        self.quit_btn.pack(side=tk.RIGHT, padx=(0, 8))

        # Faint accent rule beneath the header (a nod to the top accent glow).
        self.head_rule = tk.Frame(self.outer, height=2)
        self.head_rule.pack(fill=tk.X, padx=24, pady=(0, 2))

        self.card = tk.Frame(self.outer, bd=0, highlightthickness=1)
        self.card.pack(fill=tk.BOTH, expand=True, padx=24, pady=(6, 12))
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

        # Work schedule: run only on selected days of the week (Sun-Sat).
        self.work_var = tk.BooleanVar(value=False)
        self.work_chk = self._check(
            "Work schedule (only run on selected days)", self.work_var,
            command=self._on_work_toggle)
        self.days_row = tk.Frame(self.card_pad)
        self.days_row.pack(fill=tk.X, pady=(8, 4))
        self.day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        self.work_days = set()
        self.day_btns = []
        for i, name in enumerate(self.day_names):
            btn = tk.Button(self.days_row, text=name, font=self.f_small,
                            relief="flat", bd=0, cursor="hand2",
                            command=lambda d=i: self._toggle_day(d),
                            padx=6, pady=6, highlightthickness=1)
            btn.pack(side=tk.LEFT, expand=True, fill=tk.X,
                     padx=(0, 4) if i < 6 else 0)
            self.day_btns.append(btn)

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

        # Appearance: HSV colour-wheel accent picker.
        self.appear_hdr = tk.Label(self.card_pad, text="APPEARANCE",
                                   font=self.f_small, anchor="w")
        self.appear_hdr.pack(fill=tk.X, pady=(12, 6))
        self.accent_row = tk.Frame(self.card_pad)
        self.accent_row.pack(fill=tk.X, pady=(0, 2))
        self.accent_reset = tk.Button(self.accent_row, text="Reset",
                                      font=self.f_small, relief="flat", bd=0,
                                      cursor="hand2", command=self._reset_accent,
                                      padx=10, pady=4)
        self.accent_reset.pack(side=tk.RIGHT)
        self.accent_swatch = tk.Button(self.accent_row, text="Color",
                                       font=self.f_small, relief="flat", bd=0,
                                       cursor="hand2", command=self._pick_accent,
                                       padx=18, pady=4)
        self.accent_swatch.pack(side=tk.RIGHT, padx=(0, 8))

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
        self._section_hdrs = [self.sched_hdr, self.behav_hdr, self.appear_hdr]
        self._field_labels = [self.start_lbl, self.end_lbl, self.idle_lbl]
        self._checks = [self.work_chk, self.awake_chk, self.min_chk,
                        self.startup_chk]
        self._setup_hovers()

    def _setup_hovers(self):
        """Hover: secondary buttons raise their border to --acc2; accent-filled
        buttons brighten to --acc2."""
        for btn in (self.accent_swatch, self.banner_action):
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg=self._tok["acc2"]))
            btn.bind("<Leave>", lambda e, b=btn: b.config(bg=self._tok["acc"]))
        for btn in (self.theme_btn, self.accent_reset):
            btn.bind("<Enter>",
                     lambda e, b=btn: b.config(highlightbackground=self._tok["acc2"]))
            btn.bind("<Leave>",
                     lambda e, b=btn: b.config(highlightbackground=self._tok["line"]))
        self.toggle_btn.bind(
            "<Enter>", lambda e: self.toggle_btn.config(
                bg=DANGER_HOVER if self.engine.running else self._tok["acc2"]))
        self.toggle_btn.bind(
            "<Leave>", lambda e: self.toggle_btn.config(
                bg=DANGER if self.engine.running else self._tok["acc"]))
        # Quit: subtle red on hover to signal it fully exits the app.
        self.quit_btn.bind("<Enter>", lambda e: self.quit_btn.config(
            fg="#ffffff", bg=DANGER, highlightbackground=DANGER))
        self.quit_btn.bind("<Leave>", lambda e: self.quit_btn.config(
            fg=self._tok["dim"], bg=self._tok["panel2"],
            highlightbackground=self._tok["line"]))

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
        self.accent = normalize_hex(c.get("accent", DEFAULT_ACCENT))
        self.work_var.set(bool(c.get("work_schedule_enabled", False)))
        days = c.get("work_days", [1, 2, 3, 4, 5])
        self.work_days = set(int(d) for d in days if 0 <= int(d) <= 6)
        # Reflect the real registry state, not just stored config.
        self.startup_var.set(is_startup_enabled())

    def _collect_config(self):
        cfg = dict(self.config)
        cfg.update({
            "start_time": self.start_entry.get().strip(),
            "end_time": self.end_entry.get().strip(),
            "theme": self.theme_name,
            "accent": self.accent,
            "awake_mode": bool(self.awake_var.get()),
            "work_schedule_enabled": bool(self.work_var.get()),
            "work_days": sorted(self.work_days),
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
        t = self._tok = resolve_tokens(self.theme_name, self.accent)
        bg, panel, panel2, line = t["bg"], t["panel"], t["panel2"], t["line"]
        txt, dim = t["txt"], t["dim"]

        self.root.config(bg=bg)
        self.outer.config(bg=bg)
        self.footer.config(bg=bg, fg=dim)

        # Header band + accent rule.
        self.header.config(bg=panel)
        self.title_lbl.config(bg=panel, fg=txt)
        self.head_rule.config(bg=t["acc2"])
        self.theme_btn.config(bg=panel2, fg=txt, activebackground=panel2,
                              activeforeground=txt, highlightthickness=1,
                              highlightbackground=line, highlightcolor=line,
                              text="\u2600  Light" if self.theme_name == "dark"
                              else "\u263e  Dark")
        self.quit_btn.config(bg=panel2, fg=t["dim"], activebackground=panel2,
                             activeforeground=txt, highlightthickness=1,
                             highlightbackground=line, highlightcolor=line)

        # Card surface with 1px line border.
        self.card.config(bg=panel, highlightbackground=line,
                         highlightcolor=line)
        for w in (self.card_pad, self.time_row, self.accent_row,
                  self.status_row, self.days_row):
            w.config(bg=panel)
        for hdr in self._section_hdrs:
            hdr.config(bg=panel, fg=dim)
        for lbl in self._field_labels:
            lbl.config(bg=panel, fg=dim)
        for e in self._entries:
            e.config(bg=panel2, fg=txt, insertbackground=txt,
                     disabledbackground=panel2, highlightthickness=1,
                     highlightbackground=line, highlightcolor=t["acc2"])
            e.master.config(bg=panel)
        for chk in self._checks:
            chk.config(bg=panel, fg=txt, activebackground=panel,
                       activeforeground=txt, selectcolor=panel2)
        self.accent_reset.config(bg=panel2, fg=txt, activebackground=panel2,
                                 activeforeground=txt, highlightthickness=1,
                                 highlightbackground=line, highlightcolor=line)
        self.zen_note.config(bg=panel, fg=dim)
        self.status_dot.config(bg=panel)
        self.status_txt.config(bg=panel, fg=txt)

        # Banner: neutral panel2 surface, accent action button.
        self.banner.config(bg=panel2, highlightthickness=1,
                           highlightbackground=line)
        self.banner_lbl.config(bg=panel2, fg=txt)
        self.banner_dismiss.config(bg=panel2, fg=dim, activebackground=panel2,
                                   activeforeground=txt)

        self._apply_accent_widgets()
        self._refresh_day_buttons()
        self._refresh_toggle_visual()
        self._refresh_status()

    def _apply_accent_widgets(self):
        """Paint accent-filled widgets (Color button + banner action)."""
        t = self._tok
        for btn in (self.accent_swatch, self.banner_action):
            btn.config(bg=t["acc"], fg=t["ink"], activebackground=t["acc2"],
                       activeforeground=t["ink"])

    def toggle_theme(self):
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.apply_theme()
        self._save_ui_config()

    def _pick_accent(self):
        if WHEEL_AVAILABLE:
            AccentWheelDialog(self.root, self.accent, self._tok,
                              on_change=self._preview_accent,
                              on_commit=self._commit_accent)
        else:  # pragma: no cover - only if Pillow's Tk bridge is missing
            from tkinter import simpledialog
            hexv = simpledialog.askstring(
                "Accent color", "Hex color (#rrggbb):", initialvalue=self.accent,
                parent=self.root)
            if hexv:
                self._commit_accent(hexv)

    def _preview_accent(self, hex_color):
        """Live update while dragging the wheel (no save)."""
        self.accent = normalize_hex(hex_color)
        self.apply_theme()

    def _commit_accent(self, hex_color):
        self.accent = normalize_hex(hex_color)
        self.apply_theme()
        self._save_ui_config()

    def _reset_accent(self):
        self._commit_accent(DEFAULT_ACCENT)

    # -- work schedule -------------------------------------------------------
    def _toggle_day(self, index):
        if index in self.work_days:
            self.work_days.discard(index)
        else:
            self.work_days.add(index)
        self._refresh_day_buttons()
        self._save_ui_config()

    def _on_work_toggle(self):
        self._refresh_day_buttons()
        self._save_ui_config()

    def _refresh_day_buttons(self):
        """Colour day pills: selected = accent fill; unselected = neutral.
        When the work schedule is off, the pills are dimmed/disabled."""
        t = self._tok
        enabled = bool(self.work_var.get())
        for i, btn in enumerate(self.day_btns):
            selected = i in self.work_days
            if not enabled:
                btn.config(state="disabled", bg=t["panel2"], fg=t["dim"],
                           disabledforeground=t["dim"],
                           highlightbackground=t["line"],
                           highlightcolor=t["line"])
            elif selected:
                btn.config(state="normal", bg=t["acc"], fg=t["ink"],
                           activebackground=t["acc2"], activeforeground=t["ink"],
                           highlightbackground=t["acc"], highlightcolor=t["acc"])
            else:
                btn.config(state="normal", bg=t["panel2"], fg=t["txt"],
                           activebackground=t["panel2"], activeforeground=t["txt"],
                           highlightbackground=t["line"], highlightcolor=t["line"])

    def _refresh_toggle_visual(self):
        t = self._tok
        if self.engine.running:
            self.toggle_btn.config(text="Stop", bg=DANGER, fg="#ffffff",
                                   activebackground=DANGER_HOVER,
                                   activeforeground="#ffffff")
        else:
            self.toggle_btn.config(text="Start", bg=t["acc"], fg=t["ink"],
                                   activebackground=t["acc2"],
                                   activeforeground=t["ink"])

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
            self._set_inputs_state("normal")
            self._refresh_toggle_visual()
            return
        self._start_engine()

    def _start_engine(self):
        """Validate the schedule inputs and start jiggling. Returns True on
        success. Shared by the Start button and startup auto-start."""
        start = self.start_entry.get().strip()
        end = self.end_entry.get().strip()
        if not self._validate_time(start) or not self._validate_time(end):
            self._flash_footer("Times must be in HH:MM 24-hour format.")
            return False
        try:
            idle_threshold = float(self.idle_entry.get())
        except ValueError:
            self._flash_footer("Idle seconds must be a number.")
            return False
        if idle_threshold < 5:
            self._flash_footer("Idle seconds must be at least 5.")
            return False
        self.engine.configure(start, end, idle_threshold,
                              bool(self.awake_var.get()),
                              work_schedule_enabled=bool(self.work_var.get()),
                              work_days=set(self.work_days))
        self._save_ui_config()
        self._set_inputs_state("disabled")
        self.engine.start()
        self._refresh_toggle_visual()
        return True

    def _auto_start(self):
        """Begin jiggling automatically (used when launched at Windows
        startup). Silently no-ops if the schedule inputs are invalid."""
        if not self.engine.running:
            self._start_engine()

    def _set_inputs_state(self, state):
        for e in self._entries:
            e.config(state=state)
        for chk in self._checks:
            chk.config(state=state)
        for btn in self.day_btns:
            btn.config(state=state)
        # Restore correct day colours/enabled state when re-enabling.
        if state == "normal":
            self._refresh_day_buttons()

    # -- tray / window -------------------------------------------------------
    def hide_to_tray(self):
        if TRAY_AVAILABLE:
            self.root.withdraw()
        else:
            self.root.iconify()

    def _set_window_icon(self):
        """Set the title-bar/taskbar window icon from the rendered branded
        image. No-ops gracefully if Pillow's Tk bridge is unavailable."""
        if not (WHEEL_AVAILABLE and ICON_AVAILABLE):
            return
        try:
            img = render_icon(64, running=True)
            self._icon_photo = _WheelImageTk.PhotoImage(img)
            self.root.iconphoto(True, self._icon_photo)
        except Exception:
            pass

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
        color = self.accent if state == "active" else STATUS_COLORS.get(
            state, "#8a8a8e")
        self.status_dot.config(fg=color)
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
            if self.engine.work_schedule_enabled:
                days = self._days_summary(self.engine.work_days)
                when = f"{days} {self.engine.start_time}\u2013{self.engine.end_time}"
            else:
                when = (f"{self.engine.start_time}\u2013{self.engine.end_time} "
                        "daily")
            self.footer.config(
                text=(f"Schedule {when} \u00b7 auto-resumes \u00b7 "
                      f"screen-awake {awake}"))
        else:
            self.footer.config(
                text="Runs on your daily schedule and resumes automatically "
                     "each day.")

    def _days_summary(self, day_set):
        days = sorted(day_set)
        if not days:
            return "no days"
        if days == [1, 2, 3, 4, 5]:
            return "Mon\u2013Fri"
        if days == [0, 1, 2, 3, 4, 5, 6]:
            return "every day"
        if days == [0, 6]:
            return "weekends"
        return ", ".join(self.day_names[d] for d in days)

    def _flash_footer(self, msg):
        self.footer.config(text=msg)

    def _drain_commands(self):
        try:
            while True:
                msg = self._cmd_queue.get_nowait()
                cmd = msg[0]
                if cmd == "show":
                    self.show_window()
                elif cmd == "toggle":
                    self.toggle()
                elif cmd == "startup_toggle":
                    self.startup_var.set(not self.startup_var.get())
                    self._on_startup_checkbox()
                elif cmd == "update_available":
                    self._show_update_banner(msg[1])
                elif cmd == "dl_progress":
                    self._on_dl_progress(msg[1])
                elif cmd == "dl_done":
                    self._on_dl_done(msg[1])
                elif cmd == "dl_error":
                    self._on_dl_error(msg[1])
                elif cmd == "quit":
                    self._on_quit()
                    return
        except queue.Empty:
            pass

    # -- updater -------------------------------------------------------------
    def _bg_check_update(self):
        info = check_for_update(APP_VERSION)
        if info:
            self._cmd_queue.put(("update_available", info))

    def _show_update_banner(self, info):
        self._update_info = info
        self.banner_lbl.config(
            text="A new version (%s) is available. You have %s."
            % (info.get("version", "?"), APP_VERSION))
        if info.get("download_url"):
            self.banner_action.config(text="Download", state="normal",
                                      command=self._download_update)
        else:
            self.banner_action.config(text="View release", state="normal",
                                      command=self._open_release_page)
        self.banner.pack(fill=tk.X, side=tk.TOP, before=self.header)
        self.apply_theme()

    def _hide_banner(self):
        self.banner.pack_forget()

    def _open_release_page(self):
        info = self._update_info or {}
        webbrowser.open(info.get("html_url")
                        or "https://github.com/%s/releases" % GITHUB_REPO)

    def _download_update(self):
        info = self._update_info or {}
        url = info.get("download_url")
        if not url:
            self._open_release_page()
            return
        self.banner_action.config(state="disabled")
        dest = os.path.join(downloads_dir(),
                            info.get("asset_name") or "ZenMouseJiggler-portable.zip")

        def worker():
            try:
                download_file(
                    url, dest,
                    lambda p: self._cmd_queue.put(("dl_progress", p)))
                self._cmd_queue.put(("dl_done", dest))
            except Exception as ex:
                self._cmd_queue.put(("dl_error", str(ex)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_dl_progress(self, fraction):
        self.banner_lbl.config(text="Downloading update\u2026 %d%%"
                               % int(fraction * 100))

    def _on_dl_done(self, path):
        self._downloaded_path = path
        if getattr(sys, "frozen", False):
            self.banner_lbl.config(
                text="Update downloaded. Restart to finish installing.")
            self.banner_action.config(text="Install & Restart", state="normal",
                                      command=self._install_and_restart)
        else:
            self.banner_lbl.config(
                text="Update downloaded to your Downloads folder.")
            self.banner_action.config(text="Open folder", state="normal",
                                      command=self._reveal_download)
        self.apply_theme()

    def _install_and_restart(self):
        self.banner_action.config(state="disabled")
        self.banner_lbl.config(
            text="Installing update\u2026 the app will restart.")
        self.root.update_idletasks()
        try:
            apply_update(self._downloaded_path, app_dir())
        except Exception:
            self.banner_lbl.config(
                text="Couldn't install automatically. Click to open the folder.")
            self.banner_action.config(text="Open folder", state="normal",
                                      command=self._reveal_download)
            self.apply_theme()
            return
        # Quit so the running files unlock; the helper swaps them and relaunches.
        self.root.after(500, self._on_quit)

    def _on_dl_error(self, message):
        self.banner_lbl.config(
            text="Download failed. Click to open the release page.")
        self.banner_action.config(text="View release", state="normal",
                                  command=self._open_release_page)
        self.apply_theme()

    def _reveal_download(self):
        path = self._downloaded_path
        if not path:
            return
        try:
            if IS_WINDOWS:
                import subprocess
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:  # pragma: no cover
                webbrowser.open("file://" + os.path.dirname(path))
        except Exception:
            try:
                os.startfile(os.path.dirname(path))  # noqa: safe on Windows
            except Exception:
                pass

    def _poll_status(self):
        # Main-thread pump: drain tray commands, react to engine state.
        if getattr(self, "_quitting", False):
            return
        self._drain_commands()
        if self._last_running != self.engine.running:
            if not self.engine.running:
                self._set_inputs_state("normal")
            self._refresh_toggle_visual()
            if self.tray:
                self.tray.refresh(self.engine.running)
        self._last_running = self.engine.running
        self._refresh_status()
        self._poll_after_id = self.root.after(300, self._poll_status)

    def _on_quit(self):
        """Graceful full shutdown: stop the worker, release the screen-awake
        request and single-instance lock, stop the tray, and close the app so
        no threads, timers, or OS power requests are left behind."""
        if getattr(self, "_quitting", False):
            return
        self._quitting = True
        # 1) Stop the jiggler worker and wait briefly for it to unwind.
        try:
            self.engine.stop()
            thread = getattr(self.engine, "_thread", None)
            if thread is not None:
                thread.join(timeout=2)
        except Exception:
            pass
        # 2) Release the keep-awake power request so the PC can sleep again.
        try:
            set_keep_awake(False)
        except Exception:
            pass
        # 3) Stop the system-tray icon/thread.
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        # 4) Release the single-instance lock immediately.
        release_single_instance()
        # 5) Cancel the pending status-poll timer, then tear down the Tk loop.
        try:
            after_id = getattr(self, "_poll_after_id", None)
            if after_id is not None:
                self.root.after_cancel(after_id)
        except Exception:
            pass
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


def _parse_args(argv):
    flags = {a.lstrip("-/").lower() for a in argv[1:]}
    return "minimized" in flags or "min" in flags


def main():
    # Single-instance guard (OS file lock; safe inside OneDrive).
    guard = acquire_single_instance()
    if guard is None:
        return

    # Self-heal a stale startup entry if the folder was moved/renamed.
    reconcile_startup_entry()

    minimized_arg = _parse_args(sys.argv)
    config = load_config()
    start_minimized = minimized_arg or bool(config.get("start_minimized"))

    engine = JigglerEngine()
    root = tk.Tk()
    JigglerApp(root, engine, config=config, start_minimized=start_minimized,
               launched_at_startup=minimized_arg)
    root.mainloop()


if __name__ == "__main__":
    main()
