#!/usr/bin/env python3
"""Fullscreen answer display for the voice worker.

Launched as a SEPARATE process by voice_mode.py (macOS GUI frameworks must own
their process's main thread, so this can't live in the worker's background
thread). Reads the text to show from stdin and displays it big and centered on
a dark fullscreen window, auto-closing after POPUP_SECONDS (0 = stay until
dismissed). Press Esc or click to close early.

Env:
  POPUP_SECONDS  Seconds on screen before auto-close (default 15; 0 = stay)
  POPUP_BG       Background color   (default dark navy)
  POPUP_FG       Text color         (default near-white)
  POPUP_TITLE    Small heading above the answer (default "Asistente")
"""

import os
import sys

SECONDS = float(os.environ.get("POPUP_SECONDS", "15"))
BG = os.environ.get("POPUP_BG", "#0b0f1a")
FG = os.environ.get("POPUP_FG", "#e8eef7")
ACCENT = os.environ.get("POPUP_ACCENT", "#6ea8fe")
TITLE = os.environ.get("POPUP_TITLE", "Asistente")


def main():
    text = sys.stdin.read().strip()
    if not text:
        return
    import tkinter as tk

    root = tk.Tk()
    root.title(TITLE)
    root.configure(bg=BG)
    root.attributes("-fullscreen", True)
    try:
        root.attributes("-topmost", True)
    except Exception:  # noqa: BLE001
        pass

    sw = root.winfo_screenwidth()
    body_size = max(24, int(sw / 42))
    head_size = max(16, int(sw / 90))

    frame = tk.Frame(root, bg=BG)
    frame.pack(expand=True, fill="both", padx=48, pady=48)

    tk.Label(frame, text=TITLE.upper(), bg=BG, fg=ACCENT,
             font=("Helvetica", head_size, "bold")).pack(pady=(0, 24))
    tk.Label(frame, text=text, bg=BG, fg=FG,
             font=("Helvetica", body_size), wraplength=int(sw * 0.86),
             justify="center").pack(expand=True)

    def close(_event=None):
        root.destroy()

    root.bind("<Escape>", close)
    root.bind("<Button-1>", close)
    if SECONDS > 0:
        root.after(int(SECONDS * 1000), close)
    root.mainloop()


if __name__ == "__main__":
    main()
