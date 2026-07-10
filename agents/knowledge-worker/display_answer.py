#!/usr/bin/env python3
"""Fullscreen native (Cocoa / PyObjC) answer display for the voice assistant.

Runs as a SEPARATE process because macOS GUI frameworks must own the process's
main thread, while the voice listener lives in a background thread of worker.py.

It reads newline-delimited JSON messages from STDIN, one per answer:

    {"question": "...", "text": "..."}

and shows the latest one in a centred, resizable window with large centred
text on a dark background. The process is launched once by voice_mode and
reused; when stdin closes (worker exits) the window closes too. Press Esc /
Cmd-Q to dismiss.

Requires pyobjc (see requirements-voice.txt). Nothing here touches the network.
"""

import json
import sys
import threading

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSCenterTextAlignment,
    NSColor,
    NSFont,
    NSScreen,
    NSTextField,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSViewMinYMargin,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakeRect
from PyObjCTools import AppHelper


def _rgb(r, g, b):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255.0, g / 255.0,
                                                      b / 255.0, 1.0)


DARK = _rgb(0x0B, 0x0F, 0x1A)
ACCENT = _rgb(0x6E, 0xA8, 0xFE)
GREY = _rgb(0x8A, 0x93, 0xA6)
FG = _rgb(0xE8, 0xEE, 0xF7)

_state = {}  # holds the live NSTextField references


def _label(frame, color, size, bold):
    tf = NSTextField.alloc().initWithFrame_(frame)
    tf.setStringValue_("")
    tf.setBezeled_(False)
    tf.setDrawsBackground_(False)
    tf.setEditable_(False)
    tf.setSelectable_(False)
    tf.setTextColor_(color)
    tf.setAlignment_(NSCenterTextAlignment)
    font = (NSFont.boldSystemFontOfSize_(size) if bold
            else NSFont.systemFontOfSize_(size))
    tf.setFont_(font)
    tf.cell().setWraps_(True)
    tf.cell().setScrollable_(False)
    return tf


class KioskWindow(NSWindow):
    def canBecomeKeyWindow(self):
        return True

    def keyDown_(self, event):
        # 53 = Esc
        if event.keyCode() == 53:
            AppHelper.stopEventLoop()
        else:
            objc.super(KioskWindow, self).keyDown_(event)


def _update(question, text):
    _state["q"].setStringValue_(question or "")
    _state["a"].setStringValue_(text or "")


def _reader():
    """Stream JSON lines from stdin and push updates to the UI thread."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            q, t = msg.get("question", ""), msg.get("text", "")
        except Exception:  # noqa: BLE001
            q, t = "", line
        AppHelper.callAfter(_update, q, t)
    # stdin closed -> parent gone -> quit.
    AppHelper.callAfter(AppHelper.stopEventLoop)


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)

    screen = NSScreen.mainScreen()
    sframe = screen.frame()
    # Windowed (not fullscreen): centred, ~60% x 55% of the screen.
    w = sframe.size.width * 0.60
    h = sframe.size.height * 0.55
    x = sframe.origin.x + (sframe.size.width - w) / 2
    y = sframe.origin.y + (sframe.size.height - h) / 2
    margin = w * 0.06
    inner = w - 2 * margin

    style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
             | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
    window = KioskWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(x, y, w, h), style, NSBackingStoreBuffered, False)
    window.setTitle_("Asistente")
    window.setOpaque_(True)
    window.setBackgroundColor_(DARK)
    window.setMinSize_((420.0, 320.0))

    content = window.contentView()

    head = _label(NSMakeRect(margin, h * 0.80, inner, h * 0.10),
                  ACCENT, h * 0.045, True)
    head.setStringValue_("ASISTENTE")
    head.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
    content.addSubview_(head)

    question = _label(NSMakeRect(margin, h * 0.64, inner, h * 0.14),
                      GREY, h * 0.045, False)
    question.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
    content.addSubview_(question)

    answer = _label(NSMakeRect(margin, h * 0.08, inner, h * 0.52),
                    FG, h * 0.075, True)
    answer.setStringValue_("Escuchando\u2026")
    answer.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    content.addSubview_(answer)

    _state["q"] = question
    _state["a"] = answer

    window.center()
    window.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)

    threading.Thread(target=_reader, name="stdin", daemon=True).start()
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
