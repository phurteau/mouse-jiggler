import pyautogui
import random
import time
import threading
import tkinter as tk
from tkinter import messagebox
from datetime import datetime

class MouseJiggler:
    def __init__(self):
        self.jiggling = False
        self.jiggle_interval = 1  # Default interval in seconds
        self.start_time = "00:00"  # Default start time
        self.end_time = "23:59"  # Default end time

    def start_jiggling(self):
        self.jiggling = True
        while self.jiggling:
            current_time = datetime.now().strftime("%H:%M")
            if self.start_time <= current_time <= self.end_time:
                # Move the mouse slightly
                pyautogui.moveRel(random.randint(1, 5), random.randint(1, 5))
            time.sleep(self.jiggle_interval + random.uniform(-0.2, 0.2))  # Random delay

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
        self.root.geometry("400x300")

        self.start_time_label = tk.Label(root, text="Start Time (HH:MM):")
        self.start_time_label.pack(pady=10)

        self.start_time_entry = tk.Entry(root)
        self.start_time_entry.insert(0, "00:00")
        self.start_time_entry.pack(pady=5)

        self.end_time_label = tk.Label(root, text="End Time (HH:MM):")
        self.end_time_label.pack(pady=10)

        self.end_time_entry = tk.Entry(root)
        self.end_time_entry.insert(0, "23:59")
        self.end_time_entry.pack(pady=5)

        self.interval_label = tk.Label(root, text="Jiggle Interval (seconds):")
        self.interval_label.pack(pady=10)

        self.interval_entry = tk.Entry(root)
        self.interval_entry.insert(0, "1")
        self.interval_entry.pack(pady=5)

        self.start_button = tk.Button(root, text="Start Jiggling", command=self.start_jiggling)
        self.start_button.pack(pady=10)

        self.stop_button = tk.Button(root, text="Stop Jiggling", command=self.stop_jiggling)
        self.stop_button.pack(pady=10)

    def start_jiggling(self):
        try:
            start_time = self.start_time_entry.get()
            end_time = self.end_time_entry.get()
            interval = float(self.interval_entry.get())

            self.jiggler.set_time_window(start_time, end_time)
            self.jiggler.set_timer(interval)

            threading.Thread(target=self.jiggler.start_jiggling, daemon=True).start()
            messagebox.showinfo("Info", "Jiggling Started")
        except ValueError:
            messagebox.showerror("Error", "Please enter valid time and interval values.")

    def stop_jiggling(self):
        self.jiggler.stop_jiggling()
        messagebox.showinfo("Info", "Jiggling Stopped")


if __name__ == "__main__":
    jiggler = MouseJiggler()
    root = tk.Tk()
    gui = MouseJigglerGUI(root, jiggler)
    root.mainloop()
