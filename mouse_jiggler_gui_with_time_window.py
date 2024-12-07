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
        self.jiggle_interval = 1
        self.start_time = "00:00"
        self.end_time = "23:59"

    def start_jiggling(self):
        self.jiggling = True
        while self.jiggling:
            current_time = datetime.now().strftime("%H:%M")
            if self.start_time <= current_time <= self.end_time:  # Only jiggle within the time window
                pyautogui.moveRel(random.randint(1, 5), random.randint(1, 5))
            time.sleep(self.jiggle_interval + random.uniform(-0.2, 0.2))

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

        self.root.title("Mouse Jiggler with Time Window")
        self.root.geometry("400x350")
        self.root.config(bg="#2d7b5f")

        # Custom font
        self.custom_font = font.Font(family="Helvetica", size=12, weight="bold")

        # Frame with padding and solid border
        self.frame = tk.Frame(self.root, bg="#2d7b5f", bd=10, relief="solid", padx=20, pady=20)
        self.frame.pack(padx=20, pady=20, fill=tk.BOTH, expand=True)

        # Start Time Entry (Optional)
        self.start_time_label = tk.Label(self.frame, text="Start Time (HH:MM):", font=self.custom_font, bg="#2d7b5f", fg="white")
        self.start_time_label.pack(pady=(10, 5))
        self.start_time_entry = tk.Entry(self.frame, font=self.custom_font, bd=0, relief="solid", width=10)
        self.start_time_entry.insert(0, "00:00")
        self.start_time_entry.pack(pady=(0, 20))

        # End Time Entry (Optional)
        self.end_time_label = tk.Label(self.frame, text="End Time (HH:MM):", font=self.custom_font, bg="#2d7b5f", fg="white")
        self.end_time_label.pack(pady=(10, 5))
        self.end_time_entry = tk.Entry(self.frame, font=self.custom_font, bd=0, relief="solid", width=10)
        self.end_time_entry.insert(0, "23:59")
        self.end_time_entry.pack(pady=(0, 20))

        # Interval Entry
        self.interval_label = tk.Label(self.frame, text="Jiggle Interval (seconds):", font=self.custom_font, bg="#2d7b5f", fg="white")
        self.interval_label.pack(pady=(10, 5))
        self.interval_entry = tk.Entry(self.frame, font=self.custom_font, bd=0, relief="solid", width=10)
        self.interval_entry.insert(0, "1")
        self.interval_entry.pack(pady=(0, 20))

        # Start/Stop Button
        self.toggle_button = tk.Button(self.frame, text="Start Jiggling", font=self.custom_font, bg="#4caf50", fg="white", relief="flat", command=self.toggle_jiggling)
        self.toggle_button.pack(pady=20, ipadx=10, ipady=5, fill=tk.X)

    def toggle_jiggling(self):
        if not self.jiggler.jiggling:  # Start jiggling
            try:
                # Get the user input for the time window and interval
                start_time = self.start_time_entry.get()
                end_time = self.end_time_entry.get()
                interval = float(self.interval_entry.get())

                # Set time window and interval, even if not provided
                self.jiggler.set_time_window(start_time, end_time)
                self.jiggler.set_timer(interval)

                # Start jiggling in a separate thread
                threading.Thread(target=self.jiggler.start_jiggling, daemon=True).start()

                # Change button text and color
                self.toggle_button.config(text="Stop Jiggling", bg="#e64a19")  # Red for stop
            except ValueError:
                # Silent error handling for invalid input
                pass
        else:  # Stop jiggling
            self.jiggler.stop_jiggling()
            self.toggle_button.config(text="Start Jiggling", bg="#4caf50")  # Green for start


if __name__ == "__main__":
    jiggler = MouseJiggler()
    root = tk.Tk()
    gui = MouseJigglerGUI(root, jiggler)
    root.mainloop()
