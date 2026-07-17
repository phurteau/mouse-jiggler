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

- **Modern UI** - dark mode by default, one-click light mode, `#00ff00` accent.
- **Daily schedule** - set a start and end time; it only runs inside that
  window and **auto-resumes on its own the next day**. Overnight windows
  (e.g. `22:00`–`06:00`) are supported.
- **Zen (invisible)** - the mouse cursor never moves.
- **Awake mode** - keeps the screen from sleeping during your schedule,
  without muting Teams notifications.
- **User override + auto-reactivate** - while you're actually using the
  mouse/keyboard it stays out of the way. After a period of inactivity
  (default **60s**, configurable) it quietly re-activates.
- **System tray** - close or minimize to the tray; double-click the tray icon
  (or *Show*) to restore. Start/Stop and *Launch at startup* are on the tray
  menu too.
- **Start minimized** - optionally launch straight to the tray.
- **Launch at Windows startup** - optional; registers under the current-user
  `Run` key and starts minimized.
- **Portable** - no installer, no admin rights. Runs from any folder,
  **including inside OneDrive** (see below).
- **Live status** - a colour-coded dot shows *Active*, *Standing by
  (you're active)*, *Waiting (resumes …)*, or *Stopped*.

## Get the portable app

Download / copy **`dist/ZenMouseJiggler-portable.zip`**, extract the
`ZenMouseJiggler` folder anywhere you like (Desktop, a USB stick, or a OneDrive
folder), and run **`ZenMouseJiggler.exe`** inside it. No Python required.

Keep the whole folder together - the `.exe` needs the files next to it.

## Run from source (Python)

```bash
pip install pystray Pillow      # only needed for the system-tray icon
python mouse-jiggler.py
```

The idle-detection, invisible-activity, and keep-awake features use the
built-in Windows API via `ctypes` - no third-party packages. `pystray`/`Pillow`
are only for the tray; without them the app still runs (minimize goes to the
taskbar instead of the tray).

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

## How the schedule / override logic works

| Situation | What it does |
|---|---|
| Outside your time window | Idle - releases keep-awake, waits for the next day |
| Inside window, you're actively using the mouse | Stands by (your real input keeps you awake) |
| Inside window, idle ≥ your threshold | Sends an invisible `F15` keypress to keep you active |
| Any time inside window (awake mode on) | Screen is kept from sleeping |

## License

Open source under the MIT License.
