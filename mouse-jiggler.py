import pyautogui
import random
import time
import threading
import tkinter as tk
from tkinter import font
from datetime import datetime

class MouseJiggler:
    def __init__(self):
        self.jiggling = False
        self.jiggle_interval = 15  # Default jiggle interval to 15 seconds
        self.start_time = "00:00"  # Default start time
        self.end_time = "23:59"  # Default end time

    def start_jiggling(self):
        self.jiggling = True
        while self.jiggling:
            current_time = datetime.now().strftime("%H:%M")
            if self.start_time <= current_time <= self.end_time:
                # Get current mouse position
                current_pos = pyautogui.position()

                # Move the mouse imperceptibly (move 1px, then back)
                pyautogui.moveTo(current_pos[0] + 1, current_pos[1] + 1, duration=0.01)  # Move 1px
                pyautogui.moveTo(current_pos[0], current_pos[1], duration=0.01)  # Move back

            # Wait for user-defined interval
            time.sleep(self.jiggle_interval + random.uniform(-5, 5))  # Randomize slightly

    def stop_jiggling(self):
        self.jiggling = False

    def set_timer(self, interval):
        self.jiggle_interval = interval

    def set_time_window(self, start, end):
        self.start_time = start
        self.end_time = end


class MouseJigglerGUI:
    def __init__(self, root, jiggler):
        self.root = root
        self.jiggler = jiggler

        # Window title and size
        self.root.title("Mouse Jiggler")
        self.root.config(bg="#2d7b5f")

        # Set minimum window size to prevent it from becoming too small
        self.root.minsize(350, 350)

        # Custom font
        self.custom_font = font.Font(family="Helvetica", size=12, weight="bold")

        # Main Frame
        self.frame = tk.Frame(self.root, bg="#2d7b5f", padx=20, pady=20)
        self.frame.pack(padx=20, pady=20, fill=tk.BOTH, expand=True)

        # Time Window Inputs (Optional)
        self.start_time_label = tk.Label(self.frame, text="Start Time (HH:MM):", font=self.custom_font, bg="#2d7b5f", fg="white")
        self.start_time_label.pack(pady=5)

        self.start_time_entry = tk.Entry(self.frame, font=self.custom_font, justify="center")
        self.start_time_entry.insert(0, "00:00")
        self.start_time_entry.pack(pady=5)

        self.end_time_label = tk.Label(self.frame, text="End Time (HH:MM):", font=self.custom_font, bg="#2d7b5f", fg="white")
        self.end_time_label.pack(pady=5)

        self.end_time_entry = tk.Entry(self.frame, font=self.custom_font, justify="center")
        self.end_time_entry.insert(0, "23:59")
        self.end_time_entry.pack(pady=5)

        # Jiggler interval input
        self.interval_label = tk.Label(self.frame, text="Interval (seconds):", font=self.custom_font, bg="#2d7b5f", fg="white")
        self.interval_label.pack(pady=5)

        self.interval_entry = tk.Entry(self.frame, font=self.custom_font, justify="center")
        self.interval_entry.insert(0, "15")  # Default interval is 15 seconds
        self.interval_entry.pack(pady=5)

        # Start/Stop button
        self.toggle_button = tk.Button(self.frame, text="Start", font=self.custom_font, bg="#4caf50", fg="white", relief="flat", command=self.toggle_jiggling)
        self.toggle_button.pack(pady=20, ipadx=10, ipady=5, fill=tk.X)

    def toggle_jiggling(self):
        if not self.jiggler.jiggling:  # Start jiggling
            try:
                # Get time window from user input
                start_time = self.start_time_entry.get()
                end_time = self.end_time_entry.get()

                # Get user-defined interval time
                interval = float(self.interval_entry.get())

                # Set time window and interval
                self.jiggler.set_time_window(start_time, end_time)
                self.jiggler.set_timer(interval)  # Set the interval to user input

                # Start jiggling in a separate thread
                threading.Thread(target=self.jiggler.start_jiggling, daemon=True).start()

                # Change button text to "Stop"
                self.toggle_button.config(text="Stop", bg="#e64a19")  # Change color to red
            except ValueError:
                pass
        else:  # Stop jiggling
            self.jiggler.stop_jiggling()
            self.toggle_button.config(text="Start", bg="#4caf50")  # Change color to green


if __name__ == "__main__":
    jiggler = MouseJiggler()
    root = tk.Tk()
    gui = MouseJigglerGUI(root, jiggler)
    root.mainloop()
