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
        self.jiggle_interval = 1
        self.start_time = "00:00"
        self.end_time = "23:59"

    def start_jiggling(self):
        self.jiggling = True
        while self.jiggling:
            if self.start_time <= datetime.now().strftime("%H:%M") <= self.end_time:
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
        self.root.geometry("400x300")

        # GUI Elements
        self.start_time_entry = self.create_entry("00:00")
        self.end_time_entry = self.create_entry("23:59")
        self.interval_entry = self.create_entry("1")

        # Toggle button
        self.toggle_button = tk.Button(root, text="Start Jiggling", command=self.toggle_jiggling)
        self.toggle_button.pack(pady=20)

    def create_entry(self, default_value):
        entry = tk.Entry(self.root)
        entry.insert(0, default_value)
        entry.pack(pady=5)
        return entry

    def toggle_jiggling(self):
        if not self.jiggler.jiggling:
            try:
                self.jiggler.set_time_window(self.start_time_entry.get(), self.end_time_entry.get())
                self.jiggler.set_timer(float(self.interval_entry.get()))
                threading.Thread(target=self.jiggler.start_jiggling, daemon=True).start()
                self.toggle_button.config(text="Stop Jiggling")
                messagebox.showinfo("Info", "Jiggling Started")
            except ValueError:
                messagebox.showerror("Error", "Invalid input.")
        else:
            self.jiggler.stop_jiggling()
            self.toggle_button.config(text="Start Jiggling")
            messagebox.showinfo("Info", "Jiggling Stopped")


if __name__ == "__main__":
    jiggler = MouseJiggler()
    root = tk.Tk()
    gui = MouseJigglerGUI(root, jiggler)
    root.mainloop()
