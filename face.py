"""The face on the bar (phase 11): a small animated face where the ›
chevron was, so you can tell at a glance what Cane is doing.

  idle       open eyes, small smile, blinks now and then
  listening  wide eyes, an open mouth - the mic is on
  thinking   eyes glance up, a flat mouth
  done       happy ^ ^ eyes for a moment, then idle
  error      a small frown for a moment, then idle
  focus      narrowed eyes while a focus session runs (the resting state)
  eyes       orange eyes while screen access is reading or acting

Drawn on the bar's own canvas with plain shapes - no images, no library.
Settings > Appearance can hide it, bringing the chevron back.
"""
import random

import cane

CX, CY = 30, 42          # centre, where the chevron sat
STATES = ("idle", "listening", "thinking", "done", "error", "focus", "eyes")
SETTLE_MS = 1600         # done / error go back to resting after this
ORANGE = "#ff8a3d"


def resting():
    import focus
    return "focus" if focus.current() else "idle"


class Face:
    def __init__(self, cv):
        self.cv, self.state, self._settle, self._blink = cv, "idle", None, None
        self.draw("idle")
        self._schedule_blink()

    def enabled(self):
        return bool(cane.setting("face", True))

    def set(self, state):
        if state not in STATES:
            return
        if self._settle:
            self.cv.after_cancel(self._settle)
            self._settle = None
        self.draw(state)
        if state in ("done", "error"):
            self._settle = self.cv.after(SETTLE_MS, lambda: self.draw(resting()))

    def draw(self, state, blink=False):
        cv = self.cv
        cv.delete("face")
        self.state = state
        if not self.enabled():
            cv.create_text(CX, CY, text="›", fill=cane.ACCENT, tags="face",
                           font=("Segoe UI", 24, "bold"))
            return
        col = ORANGE if state == "eyes" else cane.ACCENT
        x1, x2, ey = CX - 6, CX + 6, CY - 3
        kw = dict(fill=col, outline="", tags="face")
        line = dict(fill=col, width=2, capstyle="round", tags="face")
        if state == "listening":
            for x in (x1, x2):
                cv.create_oval(x - 3, ey - 3, x + 3, ey + 3, **kw)
            cv.create_oval(CX - 3, CY + 4, CX + 3, CY + 10, outline=col, width=2,
                           tags="face")
        elif state == "thinking":
            for x in (x1, x2):
                cv.create_oval(x - 1, ey - 5, x + 3, ey - 1, **kw)
            cv.create_line(CX - 5, CY + 7, CX + 5, CY + 7, **line)
        elif state == "done":
            for x in (x1, x2):
                cv.create_line(x - 3, ey + 1, x, ey - 2, x + 3, ey + 1, **line)
            cv.create_arc(CX - 6, CY - 1, CX + 6, CY + 9, start=200, extent=140,
                          style="arc", outline=col, width=2, tags="face")
        elif state == "error":
            for x in (x1, x2):
                cv.create_oval(x - 2, ey - 2, x + 2, ey + 2, **kw)
            cv.create_arc(CX - 6, CY + 5, CX + 6, CY + 15, start=20, extent=140,
                          style="arc", outline=col, width=2, tags="face")
        elif state == "focus":
            for x in (x1, x2):
                cv.create_line(x - 3, ey, x + 3, ey, **line)
            cv.create_line(CX - 4, CY + 7, CX + 4, CY + 7, **line)
        else:                                    # idle, eyes
            for x in (x1, x2):
                if blink:
                    cv.create_line(x - 2, ey, x + 2, ey, **line)
                else:
                    cv.create_oval(x - 2, ey - 2, x + 2, ey + 2, **kw)
            cv.create_arc(CX - 6, CY - 1, CX + 6, CY + 9, start=200, extent=140,
                          style="arc", outline=col, width=2, tags="face")

    def _schedule_blink(self):
        self._blink = self.cv.after(random.randint(3000, 6000), self._do_blink)

    def _do_blink(self):
        """Close the eyes for 120 ms, only when resting and open-eyed."""
        try:
            if self.enabled() and self.state in ("idle", "eyes"):
                state = self.state
                self.draw(state, blink=True)
                self.cv.after(120, lambda: self.state == state and self.draw(state))
        finally:
            self._schedule_blink()
