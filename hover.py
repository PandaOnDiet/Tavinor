"""Hover to open: touch the top edge of the screen above the bar and it drops
down - no hotkey. Move away without typing and it tucks itself back.

What stops it opening by accident:
  - full-screen windows (video, games, F11) never trigger it
  - a held mouse button never triggers it: dragging a window to the top
    edge is Windows' snap-to-maximise, not a request for Tavinor
  - the cursor has to rest in the zone briefly, longer over a browser,
    whose tab strip sits right at the top edge
  - the right-most strip is left alone, where maximised windows keep their
    minimise / maximise / close buttons
"""
import time

import tavinor
import winsys

POLL = 0.05            # seconds between cursor checks
DEPTH = 2              # px from the top edge that count as "touching" it
DELAY = 0.15           # seconds of rest needed over an ordinary window
DELAY_BROWSER = 0.35   # ...and over a browser
CAPTION = 160          # px kept clear at the right for window buttons
LEAVE = 0.7            # seconds off a hover-opened bar before it tucks away
NEAR = 40              # px around the bar that still count as "on it"


def zone(screen_w, bar_w, margin):
    """(x1, x2) of the trigger strip: above the bar, minus the caption area."""
    x1 = screen_w - bar_w - margin
    return x1, max(x1 + 40, screen_w - CAPTION)


def in_zone(x, y, z):
    return 0 <= y < DEPTH and z[0] <= x < z[1]


def delay_for(fg):
    """Seconds of rest needed, or None when it must not open at all."""
    if fg["fullscreen"]:
        return None
    return DELAY_BROWSER if fg["browser"] else DELAY


def near_bar(x, y, screen_w, bar_w, bar_h, margin):
    x1 = screen_w - bar_w - margin
    return (x1 - NEAR <= x <= x1 + bar_w + NEAR
            and 0 <= y <= margin + bar_h + NEAR)


def loop(bar):
    sw = winsys.u32.GetSystemMetrics(0)          # primary screen width
    z = zone(sw, bar.W, tavinor.MARGIN)
    rest_since = away_since = None
    on, checked, slow = True, 0.0, 1.0
    while True:
        time.sleep(POLL)
        try:
            now = time.monotonic()
            if now - checked > 2:                # not 20 file reads a second
                on, checked = tavinor.setting("hover", True), now
                slow = 2.5 if tavinor.setting("hover_slow", False) else 1.0
            x, y = winsys.cursor()

            if bar.visible:
                rest_since = None
                if not (bar.hover_opened and not bar.engaged):
                    away_since = None
                    continue
                if near_bar(x, y, sw, bar.W, bar.bar_height, tavinor.MARGIN):
                    away_since = None
                elif away_since is None:
                    away_since = now
                elif now - away_since > LEAVE:
                    away_since = None
                    bar.hover_opened = False
                    bar.root.after(0, bar.hide)
                continue

            if not on or not in_zone(x, y, z) or winsys.mouse_down():
                rest_since = None
                continue
            need = delay_for(winsys.foreground())
            if need is None:
                rest_since = None
                continue
            if rest_since is None:
                rest_since = now
            elif now - rest_since >= need * slow:
                rest_since = None
                bar.visible = bar.hover_opened = True    # no double open
                bar.root.after(0, lambda: bar.show(hover=True))
        except Exception:
            rest_since = None                    # never let the loop die
