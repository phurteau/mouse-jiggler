# Mouse Jiggler with Time Window Feature

This Python script simulates a "mouse jiggler" to prevent your computer from going to sleep. The jiggling happens only during a specific time window defined by the user. The program will stop jiggling when outside the specified time range and will start again the next day at the specified start time.

## Features:
- Jiggling starts at a user-defined time window.
- Jiggling continues with random timing intervals.
- Automatically restarts the jiggling the next day at the specified start time.
- The user can specify the timer interval for jiggling in seconds.

## Requirements:
- Python 3.x
- `pyautogui` library for controlling the mouse

## Installation:

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

## License:
This project is open source and available under the MIT License.
