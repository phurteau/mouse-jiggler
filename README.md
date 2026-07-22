# Zen Mouse Jiggler

A modern, scheduled keep-awake utility for Windows that keeps you shown as
active (e.g. Microsoft Teams **Available**) and stops your screen from
sleeping - **without ever visibly moving the mouse cursor**.

## Why it's different

Most jigglers physically move the cursor and, to keep the screen awake, quietly
switch Windows into **Presentation Mode** - which is exactly what **silences your
Teams notifications**. Zen Mouse Jiggler avoids both problems:

- **Zen mode (invisible):** activity is signalled with an invisible `F15`
  keypress via the Win32 API. `F15` does nothing on a modern PC, so the cursor
  never moves and nothing on screen changes - but it resets the Windows idle
  timer, so Teams stays *Available*.
- **Awake mode (no muting):** the display is kept on with
  `SetThreadExecutionState(ES_DISPLAY_REQUIRED)`. This is **not** Presentation
  Mode, so your **notifications and sounds are never muted**.

## Features

- **Modern themed UI** - a token-based design system with a true-black dark
  theme (default) and a soft off-white light theme, one click apart.
- **Custom accent color** - an HSV color-wheel picker (hue/saturation wheel +
  brightness slider + hex input). A single accent drives every highlight;
  everything else stays neutral. Persists between runs.
- **Daily schedule** - set a start and end time; it only runs inside that
  window and **auto-resumes on its own the next day**. Overnight windows
  (e.g. `22:00`–`06:00`) are supported.
- **Work schedule** - optionally restrict it to selected days of the week
  (Sun–Sat, week starts Sunday). Defaults to Mon–Fri when enabled.
- **Zen (invisible)** - the mouse cursor never moves.
- **Awake mode** - keeps the screen from sleeping during your schedule,
  without muting Teams notifications.
- **User override + auto-reactivate** - while you're actually using the
  mouse/keyboard it stays out of the way. After a period of inactivity
  (default **60s**, configurable) it quietly re-activates.
- **System tray** - close or minimize to the tray; the tray menu has
  Show, Start/Stop, Launch at startup, and Quit. The tray icon is green while
  running and gray when stopped.
- **Graceful Quit** - a Quit button in the header fully exits the app,
  releasing the keep-awake request, the worker thread, the tray, and the
  single-instance lock.
- **Start minimized** and **Launch at Windows startup** - both optional. When
  launched at startup, the jiggler **auto-starts on your saved schedule** so it
  resumes with no clicks (a normal manual launch stays stopped until you press
  Start).
- **Built-in updater** - checks GitHub Releases on launch and shows a banner
  with one-click **Download** and (for the packaged app) **Install & Restart**,
  which applies the update and relaunches while keeping your settings.
- **Portable** - no installer, no admin rights. Runs from any folder,
  **including inside OneDrive**.
- **Live status** - a colour-coded dot shows *Active*, *Standing by
  (you're active)*, *Waiting (resumes …)*, or *Stopped*.

## Get the portable app

Download / copy **`ZenMouseJiggler-portable.zip`** from the latest
[Release](https://github.com/phurteau/mouse-jiggler/releases), extract the
`ZenMouseJiggler` folder anywhere you like (Desktop, a USB stick, or a OneDrive
folder), and run **`ZenMouseJiggler.exe`** inside it. No Python required.

Keep the whole folder together - the `.exe` needs the files next to it.

## Run from source (Python)

```bash
pip install pystray Pillow      # tray icon + colour wheel
python mouse-jiggler.py
```

The idle-detection, invisible-activity, and keep-awake features use the
built-in Windows API via `ctypes` - no third-party packages. `pystray`/`Pillow`
add the system-tray icon and the HSV colour wheel; without them the app still
runs (minimize goes to the taskbar and the picker falls back to a hex input).

Start minimized from the command line with `--minimized`.

## Build the portable app yourself

```bash
pip install pyinstaller pystray Pillow
pyinstaller mouse-jiggler.spec
```

The `ZenMouseJiggler` folder is produced in `dist/`.

> **Why a folder (onedir) and not a single .exe?**
> A single-file PyInstaller build unpacks `python3xx.dll` into `%TEMP%` at
> launch. On managed machines with an **Application Control policy** (WDAC /
> Smart App Control), loading a DLL from `%TEMP%` is **blocked**, so the
> single-file build silently fails to start. The onedir build keeps the DLLs in
> a normal folder next to the `.exe`, which the policy allows - this is what
> lets it run reliably, including from OneDrive.

## Running inside OneDrive

This app is built to run from a synced OneDrive folder:

- **Single instance** is enforced with an OS file lock kept in
  `%LOCALAPPDATA%` - *not* a lock file next to the `.exe`. Lock files beside the
  executable are a common reason other "portable" jigglers refuse to run from
  OneDrive (OneDrive locks/placeholders that file during sync).
- **Settings** are saved next to the `.exe` as `zen-jiggler-config.json`, and
  automatically fall back to `%LOCALAPPDATA%\ZenMouseJiggler\` if the
  OneDrive-side write is blocked.

## Uninstalling

The app is portable - there is no installer and no "Add/Remove Programs" entry.
It only writes two things outside its own folder, and both only matter if you
turned on **Launch at Windows startup**:

- one registry value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
  (removed automatically when you untick the box), and
- a small `%LOCALAPPDATA%\ZenMouseJiggler\` folder holding the single-instance
  lock (and a settings copy only if the beside-the-exe write was ever blocked).

There are **no** HKLM keys, scheduled tasks, Start Menu shortcuts, or services.

**Easiest way - the bundled uninstaller:** run **`Uninstall.bat`** from inside
the app folder. It closes the app, removes the startup entry, deletes
`%LOCALAPPDATA%\ZenMouseJiggler`, and then deletes the app folder itself.

**Manual way:** untick *Launch at Windows startup* in the app, then delete the
app folder, and optionally delete `%LOCALAPPDATA%\ZenMouseJiggler`.

> **Moved the folder?** No need to re-toggle anything - on launch the app
> **self-heals** the startup entry, updating it to the folder's new location so
> Launch-at-startup keeps working.

## How the schedule / override logic works

| Situation | What it does |
|---|---|
| Outside your time window / on a non-work day | Idle - releases keep-awake, waits for the next active window |
| Inside window, you're actively using the mouse | Stands by (your real input keeps you awake) |
| Inside window, idle ≥ your threshold | Sends an invisible `F15` keypress to keep you active |
| Any time inside window (awake mode on) | Screen is kept from sleeping |

## License

Open source under the MIT License.
