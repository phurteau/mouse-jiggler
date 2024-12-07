# Mouse Jiggler with Time Window Feature

This Python script simulates a "mouse jiggler" to prevent your computer from going to sleep. The jiggling happens only during a specific time window defined by the user. The program will stop jiggling when outside the specified time range and will start again the next day at the specified start time.

## Features:
- Jiggling starts at a user-defined time window.
- Jiggling continues with random timing intervals.
- Automatically restarts the jiggling the next day at the specified start time.
- The user can specify the timer interval for jiggling in seconds.

## Requirements:
- Python 3.x (if running the script version)
- `pyautogui` library for controlling the mouse

## Installation:

### Option 1: Run the Python Script
1. Install Python 3 and the required libraries:
    ```bash
    pip install pyautogui
    ```

2. Download or clone this repository to your local machine.

3. Run the script:
    ```bash
    python mouse_jiggler_gui_with_time_window.py
    ```

4. Set the timer interval and the start/end times for the jiggling.

### Option 2: Run the Standalone `.exe` Program
If you don't have Python installed or prefer running an executable, you can directly use the `.exe` file created with PyInstaller.

1. After downloading or cloning this repository, navigate to the `dist` folder where the executable is located.

2. Simply double-click on `mouse_jiggler_gui_with_time_window.exe` to run the program.

3. No installation of Python or dependencies is required. The program will launch directly from the `.exe` file, and you can set the timer interval and start/end times for jiggling through the interface.

### How to Create the `.exe` (Optional)
If you need to create the `.exe` yourself, you can use **PyInstaller**:

1. Install PyInstaller (if not installed):
    ```bash
    pip install pyinstaller
    ```

2. Generate the `.exe`:
    ```bash
    pyinstaller --onefile --windowed mouse_jiggler_gui_with_time_window.py
    ```

3. The `.exe` will be created in the `dist` folder. You can then distribute or run the `.exe` without needing Python installed.

## License:
This project is open source and available under the MIT License.
