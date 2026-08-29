#!/usr/bin/env python3
"""Renders every architecture diagram in docs/HACKSTER.md's Tier 3.

    python tools/render_diagrams.py                 all of them, SVG + PNG
    python tools/render_diagrams.py two-grids       just that one
    python tools/render_diagrams.py --list          the names
    python tools/render_diagrams.py --no-png        skip rasterising

Hand-authored SVG, same house style as docs/img/table-cutting-plan.svg:
white ground, dark strokes, one warm accent, real numbers on the drawing,
and text deliberately large relative to the boxes (small labels get
rejected).

The PNG beside each SVG exists because not every viewer renders SVG. It
is produced by wrapping the SVG in a one-line HTML page and screenshotting
it with a headless Chromium, since this machine has no rsvg, no Inkscape
and no ImageMagick. `BROWSERS` below is the search list (Chrome first:
this rig's Edge hands headless screenshots off to a running instance and
shoots its error page). Override with the HOTPOT_BROWSER environment
variable if yours lives elsewhere.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# tools/render_diagrams.py -> repo root
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "img"

BROWSERS = [
    p for p in (
        os.environ.get("HOTPOT_BROWSER"),
        os.environ.get("HOTPOT_EDGE"),  # kept for old callers
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ) if p
]

FONT = "Helvetica,Arial,sans-serif"
MONO = "Consolas,Menlo,monospace"

# Every label is drawn ~20% larger than its call-site size. Text-only footers
# under the grey rule were dropped, so the drawing has the room.
FS = 1.2

INK = "#1a1a1a"
MUTED = "#6b6b6b"
ACCENT = "#cc5500"
ACCENT_FILL = "#ffe6c7"
PANEL = "#fbf7f0"
RULE = "#b8b8b8"
COOL = "#1f6f8b"
COOL_FILL = "#dceef4"
BAD = "#b03030"
BAD_FILL = "#fadddd"
GOOD = "#2f7d43"
GOOD_FILL = "#ddefe1"


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class Svg:
    def __init__(self, w, h, title=None, subtitle=None):
        self.w, self.h = w, h
        self.parts = []
        self.parts.append(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}">')
        self.parts.append(f'<rect width="{w}" height="{h}" fill="#ffffff"/>')
        self.defs_arrow()
        if title:
            self.text(40, 52, title, 27, INK, weight="bold")
        if subtitle:
            self.text(40, 82, subtitle, 17, MUTED)

    def defs_arrow(self):
        self.parts.append(
            '<defs>'
            '<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M 0 0 L 10 5 L 0 10 z" fill="#1a1a1a"/></marker>'
            '<marker id="ao" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M 0 0 L 10 5 L 0 10 z" fill="#cc5500"/></marker>'
            '<marker id="ac" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M 0 0 L 10 5 L 0 10 z" fill="#1f6f8b"/></marker>'
            '<marker id="ab" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M 0 0 L 10 5 L 0 10 z" fill="#b03030"/></marker>'
            '</defs>')

    # -- primitives -------------------------------------------------------

    def rect(self, x, y, w, h, fill="none", stroke=INK, sw=2.2, rx=6,
             dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def line(self, x1, y1, x2, y2, stroke=INK, sw=2.0, dash=None, arrow=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        m = f' marker-end="url(#{arrow})"' if arrow else ""
        self.parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" '
            f'stroke-width="{sw}" stroke-linecap="round"{d}{m}/>')

    def path(self, d, stroke=INK, sw=2.0, fill="none", dash=None, arrow=None):
        da = f' stroke-dasharray="{dash}"' if dash else ""
        m = f' marker-end="url(#{arrow})"' if arrow else ""
        self.parts.append(
            f'<path d="{d}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{sw}" stroke-linecap="round"{da}{m}/>')

    def circle(self, cx, cy, r, fill="none", stroke=INK, sw=2.0):
        self.parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"/>')

    def text(self, x, y, s, size=18, fill=INK, anchor="start", weight="normal",
             font=None, italic=False, scale=FS):
        st = ' font-style="italic"' if italic else ""
        self.parts.append(
            f'<text x="{x}" y="{y}" font-family="{font or FONT}" '
            f'font-size="{size * scale:g}" fill="{fill}" text-anchor="{anchor}" '
            f'font-weight="{weight}"{st}>{esc(s)}</text>')

    # -- composites -------------------------------------------------------

    def box(self, x, y, w, h, lines, fill=PANEL, stroke=INK, sw=2.2,
            head_size=21, body_size=16, head_fill=None, body_fill=MUTED,
            rx=8, dash=None, head_font=None, body_font=None, top=None):
        """A labelled box. `lines[0]` is the heading, the rest are notes."""
        # A box's own labels are already large relative to its walls; the FS
        # bump is for the free-standing captions and pin labels, not here.
        self.rect(x, y, w, h, fill=fill, stroke=stroke, sw=sw, rx=rx, dash=dash)
        cx = x + w / 2.0
        n = len(lines)
        gap = body_size + 6
        block = head_size + (gap * (n - 1) if n > 1 else 0)
        ty = (top if top is not None
              else y + (h - block) / 2.0 + head_size * 0.82)
        self.text(cx, ty, lines[0], head_size, head_fill or stroke,
                  anchor="middle", weight="bold", font=head_font, scale=1)
        for i, s in enumerate(lines[1:], start=1):
            self.text(cx, ty + head_size * 0.2 + gap * i, s, body_size,
                      body_fill, anchor="middle", font=body_font, scale=1)

    def caption(self, x, y, lines, size=16, fill=MUTED, anchor="start",
                gap=None, weight="normal", font=None):
        gap = (gap or size + 6) * FS
        for i, s in enumerate(lines):
            self.text(x, y + gap * i, s, size, fill, anchor=anchor,
                      weight=weight, font=font)

    def label_on_line(self, x, y, s, size=16, fill=INK, anchor="middle",
                      pad=6, weight="bold", bg="#ffffff", scale=FS):
        sz = size * scale
        w = len(s) * sz * 0.56 + pad * 2
        lx = {"middle": x - w / 2, "start": x - pad, "end": x - w + pad}[anchor]
        self.parts.append(
            f'<rect x="{lx}" y="{y - sz * 0.86}" width="{w}" '
            f'height="{sz * 1.35}" fill="{bg}" rx="3"/>')
        self.text(x, y, s, size, fill, anchor=anchor, weight=weight, scale=scale)

    def save(self, name):
        self.parts.append("</svg>")
        p = OUT / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(self.parts), encoding="utf-8")
        print(f"wrote {p.relative_to(ROOT)}  ({self.w}x{self.h})")
        return p


# ---------------------------------------------------------------- 1. processes
def processes():
    W, H = 1200, 1280
    s = Svg(W, H, "Five processes and four transports",
            "run.py starts every one of them and waits for each to say it is ready before starting the next.")

    L, R, CW = 100, 600, 460
    FULL = 960

    # hardware
    s.box(L, 125, CW, 88, ["Lenovo 300 FHD webcam",
                           "1080p, 95 degree lens, mounted at 180 degrees"],
          fill=COOL_FILL, stroke=COOL, head_fill=COOL)
    s.box(R, 125, CW, 88, ["XIAO ESP32S3 and 8 x HX711",
                           "one line of eight counts per conversion cycle"],
          fill=COOL_FILL, stroke=COOL, head_fill=COOL)

    # camera
    s.line(L + CW / 2, 213, L + CW / 2, 258, arrow="ac", stroke=COOL, sw=2.4)
    s.label_on_line(L + CW / 2 + 96, 242, "USB 3", 16, COOL)
    s.box(L, 260, CW, 96, ["camera",
                           "owns the device and the frame ring",
                           "locked exposure and white balance"])

    # ring
    s.line(L + CW / 2, 356, L + CW / 2, 400, arrow="a", sw=2.4)
    s.box(L, 402, CW, 92, ["shared memory:  hotpot_frames",
                           "8 slots, 1920 x 1080 BGR, about 47 MB",
                           "one writer, two readers, no lock"],
          fill=ACCENT_FILL, stroke=ACCENT, head_fill=ACCENT, head_font=MONO,
          head_size=19)

    # tracker / classifier
    s.line(L + 115, 494, L + 115, 556, arrow="ao", stroke=ACCENT, sw=2.4)
    s.line(L + 345, 494, L + 345, 556, arrow="ao", stroke=ACCENT, sw=2.4)
    s.box(L, 558, 225, 118, ["tracker",
                             "MediaPipe Hands",
                             "one hand, smoothed,",
                             "60 datagrams a second"], head_size=20)
    s.box(L + 235, 558, 225, 118, ["classifier",
                                   "crops each bin and",
                                   "labels what is in it",
                                   "(see the last section)"], head_size=20)

    # core
    s.box(L, 740, FULL, 132,
          ["core",
           "the TCP server every other process dials into: the state machine, the cart, the prices,",
           "the bin map, the geometry, the load cells, the order database and the staff dashboard"],
          fill="#f2ede4", head_size=26, body_size=17)

    # serial down the right edge
    s.path(f"M {R + CW / 2} 213 L {R + CW / 2} 690 L {L + FULL - 150} 690 L {L + FULL - 150} 738",
           stroke=COOL, sw=2.4, arrow="ac")
    # squeezed between the serial rail and the MJPEG line: no room for the bump
    s.label_on_line(R + CW / 2 + 168, 420, "USB serial, 115200 baud", 16, COOL,
                    scale=1)
    s.label_on_line(R + CW / 2 + 168, 444, "about 10.7 lines a second", 16,
                    COOL, scale=1)

    # tracker -> core (TCP)
    s.line(L + 115, 676, L + 115, 738, arrow="a", sw=2.4)
    s.label_on_line(L + 115, 712, "TCP 8765", 16)
    # classifier -> core
    s.line(L + 345, 676, L + 345, 738, arrow="a", sw=2.4)
    s.label_on_line(L + 345, 712, "TCP 8765", 16)

    # of + browser
    s.box(L, 950, CW, 132,
          ["of  (openFrameworks)",
           "draws the table exactly as core",
           "describes it, sixty times a second.",
           "Every word arrives already translated."],
          head_size=24)
    s.box(R, 950, CW, 132,
          ["staff dashboard",
           "any browser on the network,",
           "a phone included.",
           "Bins, Setup, Capture, Developer."],
          head_size=24)

    s.line(L + 250, 872, L + 250, 946, arrow="a", sw=2.6)
    s.label_on_line(L + 250, 906, "TCP 8765:  state, 60 times a second", 16)
    s.line(R + 250, 872, R + 250, 946, arrow="a", sw=2.6)
    s.label_on_line(R + 250, 906, "HTTP and WebSocket, port 8090", 16)

    # tracker -> of, UDP, down the far left
    s.path(f"M {L} 620 L 46 620 L 46 1000 L {L - 4} 1000",
           stroke=ACCENT, sw=2.6, arrow="ao")
    s.caption(58, 796, ["UDP 8770", "cursor", "datagrams"], 16, ACCENT)
    # tracker -> core UDP
    s.line(L + 175, 676, L + 175, 738, arrow="ao", stroke=ACCENT, sw=2.2)
    s.label_on_line(L + 175 + 55, 700, "UDP 8771", 15, ACCENT)

    # camera -> browser MJPEG
    s.path(f"M {L + CW} 308 L {R + CW + 44} 308 L {R + CW + 44} 1016 L {R + CW + 4} 1016",
           stroke=COOL, sw=2.2, arrow="ac", dash="7 6")
    s.label_on_line(R + CW - 46, 288, "MJPEG, port 8081", 16, COOL)

    # projector
    s.box(L, 1150, CW, 88, ["WZATCO projector",
                            "1920 x 1080, keystoned on the projector itself"],
          fill=COOL_FILL, stroke=COOL, head_fill=COOL)
    s.line(L + 250, 1082, L + 250, 1146, arrow="ac", stroke=COOL, sw=2.4)
    s.label_on_line(L + 250, 1122, "HDMI", 16, COOL)

    s.save("architecture-processes.svg")


# ------------------------------------------------------------- 2. cursor drain
def drain():
    W, H = 1180, 950
    s = Svg(W, H, "Why the cursor goes over UDP",
            "Six datagrams sent while the render thread is stalled, and what each transport does with them.")

    x0, x1 = 140, 1040
    def tx(ms):
        return x0 + (x1 - x0) * ms / 340.0

    seqs = [(30, 1), (68, 2), (106, 3), (144, 4), (182, 5), (218, 6)]

    def panel(top, title, colour):
        s.text(60, top + 30, title, 23, colour, weight="bold")
        s.rect(tx(12), top + 55, tx(228) - tx(12), 82, fill="#f4f1ec",
               stroke=RULE, sw=1.4, rx=5, dash="6 6")
        s.text((tx(12) + tx(228)) / 2, top + 78, "the render thread stalls for 200 ms",
               17, MUTED, anchor="middle")
        for ms, n in seqs:
            s.circle(tx(ms), top + 112, 21, fill=ACCENT_FILL, stroke=ACCENT, sw=2.2)
            s.text(tx(ms), top + 119, str(n), 19, ACCENT, anchor="middle",
                   weight="bold")
        ay = top + 178
        s.line(x0 - 20, ay, x1 + 20, ay, stroke=INK, sw=2.0)
        for ms in (0, 100, 200, 300):
            s.line(tx(ms), ay - 7, tx(ms), ay + 7, stroke=MUTED, sw=1.6)
            s.label_on_line(tx(ms), ay + 32, f"{ms} ms", 16, MUTED, weight="normal")
        return ay

    # panel 1: queued
    top = 120
    ay = panel(top, "If cursors were queued, the way TCP queues them", BAD)
    for i, (ms, n) in enumerate(seqs):
        dx = tx(238) + i * 46
        s.path(f"M {tx(ms)} {top + 134} C {tx(ms)} {top + 210}, {dx} {top + 190}, {dx} {top + 246}",
               stroke=BAD, sw=1.6, dash="5 5")
        s.circle(dx, top + 270, 21, fill=BAD_FILL, stroke=BAD, sw=2.0)
        s.text(dx, top + 277, str(n), 19, BAD, anchor="middle", weight="bold")
    s.caption(60, top + 344, [
        "All six arrive on the frame after the stall, in order. The hand visibly replays 200 ms of its own history.",
    ], 19, BAD, gap=28)

    # panel 2: drained
    top = 560
    ay = panel(top, "Drain to latest, which is what runs", GOOD)
    for ms, n in seqs[:-1]:
        s.line(tx(ms), top + 134, tx(ms), top + 246, stroke=MUTED, sw=1.6,
               dash="5 5")
        s.text(tx(ms), top + 288, "discard", 16, MUTED, anchor="middle")
    kx = tx(seqs[-1][0])
    s.line(kx, top + 134, kx, top + 246, stroke=GOOD, sw=2.6)
    s.circle(kx, top + 270, 22, fill=GOOD_FILL, stroke=GOOD, sw=2.6)
    s.text(kx, top + 277, "6", 20, GOOD, anchor="middle", weight="bold")
    s.caption(60, top + 344, [
        "Read the socket until it is empty, keep the highest seq. The hand appears where it actually is.",
    ], 19, GOOD, gap=28)

    s.save("architecture-cursor-drain.svg")


# ------------------------------------------------------------------ 3. the ring
def ring():
    W, H = 1240, 850
    s = Svg(W, H, "The frame ring in shared memory",
            "One writer, two readers, no lock. The order of the three writes is the whole mechanism.")

    s.box(80, 120, 480, 96, ["header,  64 bytes",
                             "magic HPTF, version 3, 1920, 1080, 3, 8",
                             "write_counter : u64"],
          fill=ACCENT_FILL, stroke=ACCENT, head_fill=ACCENT, body_size=16)

    s.text(660, 150, "camera writes, in this order:", 21, INK, weight="bold")
    s.caption(660, 186, [
        "1.  the pixels into the slot",
        "2.  the slot header (frame_id, ts_ns)",
        "3.  write_counter, last",
    ], 19, INK, gap=32)
    s.text(660, 300, "Step 3 is what publishes the frame, so it", 17, MUTED)
    s.text(660, 324, "happens after everything it publishes.", 17, MUTED)

    sx, sy, sw_, sh = 80, 380, 118, 150
    for i in range(8):
        x = sx + i * (sw_ + 12)
        fill = PANEL if i != 5 else ACCENT_FILL
        st = INK if i != 5 else ACCENT
        s.rect(x, sy, sw_, 46, fill=fill, stroke=st, sw=2.0, rx=5)
        s.text(x + sw_ / 2, sy + 22, "frame_id", 14, MUTED, anchor="middle")
        s.text(x + sw_ / 2, sy + 39, "ts_ns", 14, MUTED, anchor="middle")
        s.rect(x, sy + 52, sw_, sh - 6, fill=fill, stroke=st, sw=2.0, rx=5)
        s.text(x + sw_ / 2, sy + 112, f"slot {i}", 20, st, anchor="middle",
               weight="bold")
        s.text(x + sw_ / 2, sy + 138, "6.2 MB", 15, MUTED, anchor="middle")
        s.text(x + sw_ / 2, sy + 158, "of pixels", 15, MUTED, anchor="middle")

    s.text(80, 356, "8 slots, each one whole 1920 x 1080 BGR frame", 18, MUTED)

    wx = sx + 5 * (sw_ + 12) + sw_ / 2
    s.line(wx, 340, wx, 374, stroke=ACCENT, sw=2.6, arrow="ao")

    ry = 600
    s.box(120, ry, 420, 216,
          ["a reader (tracker, classifier)",
           "",
           "read write_counter",
           "read frame_id",
           "copy 6.2 MB of pixels out",
           "read frame_id again",
           "if the two disagree, retry"],
          head_size=21, body_size=18, body_fill=INK, top=ry + 40)
    s.line(wx, sy + 196, 330, ry - 6, stroke=INK, sw=2.2, arrow="a", dash="6 5")

    s.box(640, ry, 520, 216,
          ["Why the retry is there at all",
           "",
           "At 8 slots and 30 frames a second a reader has",
           "about 260 ms before the writer laps it, so a torn",
           "read should never happen. A check that cannot fail",
           "proves nothing, so the tests force one by hand."],
          head_size=21, body_size=17, body_fill=INK, top=ry + 40)

    s.save("architecture-frame-ring.svg")


# ---------------------------------------------------------- 4. coordinate spaces
def spaces():
    W, H = 1100, 1010
    s = Svg(W, H, "Four coordinate spaces",
            "Stage pixels are canonical. Everything on the wire is already in them.")

    X, CW = 90, 920

    s.box(X, 120, CW, 150,
          ["1.  the table, in millimetres",
           "1524.0 wide by 914.4 deep, which is 60 by 36 inches.",
           "Origin at the far-left corner, +y running toward the diner.",
           "TableGeometry.h holds the layout and a static_assert per chain."],
          head_size=24, body_size=18, body_fill=INK)

    s.line(X + CW / 2, 270, X + CW / 2, 336, arrow="a", sw=2.6)
    s.label_on_line(X + CW / 2, 312, "two scales, one per axis", 17)

    s.box(X, 338, CW, 172,
          ["2.  the stage, in pixels",
           "1920 by 1080.   1.25984 px per mm across,   1.18110 px per mm deep.",
           "Two scales rather than one, because the table's aspect is 1.667 and",
           "the projector's is 1.778. A single uniform scale puts the near row",
           "50 mm out, which on plywood is a visible finger's width."],
          head_size=24, body_size=18, body_fill=INK, fill=ACCENT_FILL,
          stroke=ACCENT, head_fill=ACCENT)

    s.line(X + CW / 2, 510, X + CW / 2, 576, arrow="a", sw=2.6)

    s.box(X, 578, CW, 178,
          ["3.  the camera, in pixels",
           "1920 by 1080, mounted upside down. A 3 x 3 homography H maps it onto",
           "the stage, solved from four corners a human drags onto the live feed.",
           "The corner roles are pinned to the drag order, never inferred from where",
           "a handle lands: on a camera at 180 degrees, inferring pairs every corner",
           "with its opposite, and four points always fit exactly, so the wrong",
           "answer comes back with zero error and no warning."],
          head_size=24, body_size=18, body_fill=INK)

    s.line(X + CW / 2, 756, X + CW / 2, 822, arrow="a", sw=2.6)

    s.box(X, 824, CW, 150,
          ["4.  the fluid, in two more",
           "The fire's density field is 1280 by 720 and its simulation grid is 640 by 360.",
           "Half scale, and this is the resolution pair the whole last section is about:",
           "two shaders read across it without saying so."],
          head_size=24, body_size=18, body_fill=INK)

    s.save("architecture-coordinate-spaces.svg")


# ------------------------------------------------------------------ 5. two grids
def grids():
    W, H = 1160, 995
    s = Svg(W, H, "Two bin grids, never derived from each other",
            "Four horizontal lines and eight vertical ones. A line belongs to a whole row or a whole column.")

    # the grid mechanic, drawn once at the top
    gx, gy, gw, gh = 120, 130, 920, 300
    s.rect(gx, gy, gw, gh, fill=PANEL, stroke=RULE, sw=1.8, rx=4)
    hs = [gy + 40, gy + 130, gy + 170, gy + 260]
    vs = [gx + 60, gx + 220, gx + 250, gx + 410,
          gx + 510, gx + 670, gx + 700, gx + 860]
    for i in range(8):
        col, row = i % 4, i // 4
        x0, x1 = vs[2 * col], vs[2 * col + 1]
        y0, y1 = hs[2 * row], hs[2 * row + 1]
        s.rect(x0, y0, x1 - x0, y1 - y0, fill=ACCENT_FILL, stroke=ACCENT,
               sw=1.8, rx=4)
        s.text((x0 + x1) / 2, (y0 + y1) / 2 + 9, str(i), 24, ACCENT,
               anchor="middle", weight="bold")
    for y in hs:
        s.line(gx - 46, y, gx + gw + 46, y, stroke=INK, sw=2.0, dash="8 5")
    for x in vs:
        s.line(x, gy - 26, x, gy + gh + 26, stroke=INK, sw=2.0, dash="8 5")
    s.text(gx - 56, hs[0] + 6, "4 lines", 17, INK, anchor="end", weight="bold")
    s.text(gx - 56, hs[0] + 28, "across", 17, INK, anchor="end")
    s.text(gx + gw / 2, gy + gh + 66, "8 lines down", 17, INK, anchor="middle",
           weight="bold")
    s.text(gx + gw / 2, gy + gh + 86,
           "Drag one line and the whole row or column it belongs to moves with it, "
           "so two bins in a row always share an edge.",
           17, MUTED, anchor="middle")

    # two stores
    top = 600
    s.box(80, top, 480, 340,
          ["state/bin_grid_camera.json", "", "", "", "", "", "", ""],
          fill=COOL_FILL, stroke=COOL, head_fill=COOL, head_size=21,
          head_font=MONO, top=top + 42)
    s.caption(110, top + 92, [
        "Dragged onto the rectified preview:",
        "the camera frame warped through H,",
        "which is the same picture the",
        "classifier crops from.",
        "",
        "Read by the classifier's crops and by",
        "core's hand-inside-a-bin hit test.",
        "",
        "Needs the camera and the four corners.",
    ], 18, INK, gap=25)

    s.box(600, top, 480, 340,
          ["state/bin_grid_projector.json", "", "", "", "", "", "", ""],
          fill=ACCENT_FILL, stroke=ACCENT, head_fill=ACCENT, head_size=21,
          head_font=MONO, top=top + 42)
    s.caption(630, top + 92, [
        "Nudged with the arrow keys while",
        "watching the actual light land on the",
        "actual trays. One pixel a press, ten",
        "with Shift, and every press ships.",
        "",
        "Read by openFrameworks for the halo,",
        "the white cutout and the fire ring.",
        "",
        "Needs no camera at all.",
    ], 18, INK, gap=25)

    s.line(320, 552, 320, top - 4, stroke=COOL, sw=2.4, arrow="ac")
    s.line(840, 552, 840, top - 4, stroke=ACCENT, sw=2.4, arrow="ao")
    s.text(580, 562, "same shape,", 18, MUTED, anchor="middle", weight="bold")
    s.text(580, 586, "two files", 18, MUTED, anchor="middle", weight="bold")

    s.save("architecture-two-grids.svg")


# ------------------------------------------------------------------------ 6. fsm
def fsm():
    W, H = 1200, 970
    s = Svg(W, H, "The state machine",
            "Eight states. Three edges end a session, and all three call the same function to do it.")

    def node(x, y, w, h, label, fill=PANEL, stroke=INK, size=25):
        s.rect(x, y, w, h, fill=fill, stroke=stroke, sw=2.6, rx=10)
        s.text(x + w / 2, y + h / 2 + size * 0.34, label, size, stroke,
               anchor="middle", weight="bold")

    CX, NW, NH = 700, 300, 74
    LX = 560          # chain left edge
    RX = 860          # chain right edge

    node(CX - NW / 2, 120, NW, NH, "BOOT")
    node(80, 250, 300, NH, "UNCALIBRATED", fill="#efe8fb", stroke="#6a4bbd",
         size=23)
    node(LX, 250, NW, NH, "IDLE", fill=GOOD_FILL, stroke=GOOD)
    node(LX, 400, NW, NH, "SELECTING", fill=GOOD_FILL, stroke=GOOD)
    node(LX, 560, NW, NH, "BROTH")
    node(LX, 700, NW, NH, "SPICE")
    node(LX, 840, NW, NH, "CHECKOUT")
    node(80, 700, 300, NH, "SETTING", fill="#fdf0cf", stroke="#a5761b")

    # boot
    s.line(CX - 70, 194, 250, 246, arrow="a", sw=2.4)
    s.line(CX + 40, 194, 690, 246, arrow="a", sw=2.4)
    s.text(244, 222, "no geometry saved", 16, MUTED, anchor="end")
    s.text(700, 222, "geometry on disk", 16, MUTED)
    s.path("M 380 268 C 460 268, 480 268, 556 276", stroke=INK, sw=2.4,
           arrow="a")
    s.label_on_line(468, 258, "calibrated", 15, MUTED, weight="normal")

    # idle -> selecting
    s.line(CX, 324, CX, 396, arrow="a", sw=2.6)
    s.label_on_line(CX + 140, 364, "a hand arrives", 16)

    # forward chain, labels to the LEFT
    for y0, y1, lab in ((474, 556, "Next"), (634, 696, "Next"), (774, 836, "Pay")):
        s.line(CX - 40, y0, CX - 40, y1, arrow="a", sw=2.6)
        s.text(CX - 56, (y0 + y1) / 2 + 6, lab, 17, INK, anchor="end",
               weight="bold")
    # back chain, labels to the RIGHT
    for y0, y1 in ((556, 478), (696, 638), (836, 778)):
        s.line(CX + 60, y0, CX + 60, y1, arrow="ac", stroke=COOL, sw=2.2)
        s.text(CX + 76, (y0 + y1) / 2 + 6, "Back", 17, COOL)

    # paid, up the inner-left corridor
    s.path(f"M {LX} 877 C 452 877, 448 300, {LX - 4} 292", stroke=GOOD,
           sw=2.6, arrow="a")
    s.text(548, 934, "paid", 18, GOOD, anchor="end", weight="bold")

    # cancel, a rail down the right
    s.line(1050, 437, 1050, 877, stroke=BAD, sw=2.4, dash="7 5")
    for y in (437, 597, 737, 877):
        s.line(RX, y, 1050, y, stroke=BAD, sw=1.8, dash="7 5")
    s.path(f"M 1050 437 C 1050 340, 1000 292, {RX + 4} 288", stroke=BAD,
           sw=2.4, arrow="ab", dash="7 5")
    s.text(1064, 660, "Cancel", 17, BAD, weight="bold")
    s.text(1064, 684, "offered on", 16, BAD)
    s.text(1064, 706, "all four", 16, BAD)

    # setting: in
    s.path("M 556 460 C 480 480, 430 660, 388 716", stroke="#a5761b", sw=2.4,
           arrow="a")
    s.text(400, 560, "Serving off", 17, "#a5761b", anchor="end", weight="bold")
    s.text(400, 584, "from any state,", 16, "#a5761b", anchor="end")
    s.text(400, 606, "with an empty cart", 16, "#a5761b", anchor="end")
    # setting: out
    s.path("M 150 700 L 150 332", stroke="#a5761b", sw=2.4, arrow="a")
    s.text(100, 520, "Serving on,", 16, "#a5761b")
    s.text(100, 542, "geometry", 16, "#a5761b")
    s.text(100, 564, "still missing", 16, "#a5761b")
    s.path("M 330 700 C 400 660, 470 380, 554 312", stroke="#a5761b", sw=2.4,
           arrow="a")
    s.text(352, 690, "Serving on", 16, "#a5761b", weight="bold")

    s.save("architecture-state-machine.svg")


# --------------------------------------------------------------- 7. cart weights
def weights():
    W, H = 1240, 670
    s = Svg(W, H, "Price is a subtraction, never an accumulation",
            "One bin, one session. Three arrays of eight floats, and every price derived fresh from two of them.")

    ox, oy, pw, ph = 150, 150, 940, 440
    s.rect(ox, oy, pw, ph, fill="#fcfaf6", stroke=RULE, sw=1.4, rx=4)

    # y axis: 400g at top, 300g at bottom
    def gy(g):
        return oy + ph - (g - 280) * (ph / 140.0)
    for g in (300, 320, 340, 360, 380, 400):
        y = gy(g)
        s.line(ox, y, ox + pw, y, stroke="#e6e0d6", sw=1.2)
        s.text(ox - 16, y + 6, f"{g} g", 16, MUTED, anchor="end")

    def tx(t):
        return ox + pw * t / 100.0

    # live_g: starts 400, a 60g pick around t=25, a 20g put-back around t=62
    import math
    pts = []
    for i in range(0, 101):
        t = i
        if t < 25:
            v = 400
        elif t < 32:
            v = 400 - 60 * (t - 25) / 7.0
        elif t < 62:
            v = 340
        elif t < 68:
            v = 340 + 20 * (t - 62) / 6.0
        else:
            v = 360
        v += 1.6 * math.sin(i * 1.7) + 1.1 * math.sin(i * 0.9 + 1.0)
        pts.append((tx(t), gy(v)))
    s.path("M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts),
           stroke=COOL, sw=2.4)

    # shown_g follows with the 10g deadband -> a step
    step = []
    for i in range(0, 101):
        t = i
        v = 400 if t < 29 else (340 if t < 65 else 360)
        step.append((tx(t), gy(v)))
    s.path("M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in step),
           stroke=ACCENT, sw=3.0)

    # start_g flat at 400
    s.line(ox, gy(400), ox + pw, gy(400), stroke=GOOD, sw=3.0, dash="10 6")

    lx, ly = tx(4), gy(316)
    for i, (col, name, note, dash) in enumerate((
            (GOOD, "start_g", "the weight when this session began", "10 6"),
            (COOL, "live_g", "what the cell reads right now", None),
            (ACCENT, "shown_g", "the number drawn on the table", None))):
        y = ly + i * 34
        s.line(lx, y - 6, lx + 46, y - 6, stroke=col, sw=3.2, dash=dash)
        s.text(lx + 60, y, name, 18, col, weight="bold", font=MONO)
        s.text(lx + 172, y, note, 17, MUTED)

    # removed bracket
    s.line(tx(46), gy(400), tx(46), gy(340), stroke=INK, sw=2.0)
    s.line(tx(45), gy(400), tx(47), gy(400), stroke=INK, sw=2.0)
    s.line(tx(45), gy(340), tx(47), gy(340), stroke=INK, sw=2.0)
    s.label_on_line(tx(46) + 96, gy(370) + 6, "removed = 60 g", 17)

    s.text(ox, oy + ph + 40, "time", 17, MUTED)
    s.text(tx(28), oy + ph + 40, "a scoop taken out", 17, MUTED, anchor="middle")
    s.text(tx(65), oy + ph + 40, "some of it tipped back", 17, MUTED,
           anchor="middle")

    s.save("architecture-cart-weights.svg")


# --------------------------------------------------------------- 8. scale filter
def scalefilter():
    W, H = 1160, 840
    s = Svg(W, H, "Ten and a half readings a second",
            "The architecture document said 78 Hz. The rig says 10.7, and every number below follows from that.")

    X, CW = 90, 620

    def stage(y, h, lines, note=None, fill=PANEL, stroke=INK, head=21):
        s.box(X, y, CW, h, lines, fill=fill, stroke=stroke, head_size=head,
              body_size=17, body_fill=INK, head_fill=stroke)
        if note:
            s.caption(X + CW + 30, y + 38, note, 17, MUTED, gap=25)

    stage(130, 92, ["the serial line",
                    "raw 83422 -211904 84001 82755 ..."],
          ["one line per conversion cycle",
           "115200 baud, 8 signed integers"],
          fill=COOL_FILL, stroke=COOL)

    s.line(X + CW / 2, 222, X + CW / 2, 268, arrow="a", sw=2.4)
    s.label_on_line(X + CW / 2 + 150, 252, "one sample every 93 ms", 16, MUTED,
                    weight="normal")

    stage(270, 92, ["median of the last 5 samples",
                    "discards a single bad HX711 read"],
          ["spans 465 ms of wall clock,",
           "takes about 280 ms to cross a step"])

    s.line(X + CW / 2, 362, X + CW / 2, 404, arrow="a", sw=2.4)

    stage(406, 96, ["moving average of the last 3 medians",
                    "smooths the wobble a median cannot"],
          ["the residual noise is not an outlier,",
           "so a median has nothing to drop"],
          fill=ACCENT_FILL, stroke=ACCENT)

    s.line(X + CW / 2, 502, X + CW / 2, 544, arrow="a", sw=2.4)

    stage(546, 92, ["counts to grams, per bin",
                    "the calibration set on the Bins tab"],
          ["an uncalibrated bin answers None,",
           "so it contributes nothing to a price"])

    s.line(X + CW / 2, 638, X + CW / 2, 680, arrow="a", sw=2.4)

    stage(682, 116, ["one slot, under one lock",
                     "the newest reading, overwriting the last",
                     "read by the 60 Hz loop"],
          ["a queue would let the table bill",
           "from weights that are seconds old.",
           "Reading one sample nine times in a",
           "row is correct at 10.7 Hz"])

    s.save("architecture-scale-filter.svg")


# ------------------------------------------------------------------- 9. the FBO
def fbo():
    W, H = 1180, 815
    s = Svg(W, H, "The light pass is drawn last, and that is the whole trick",
            "Five layers into one framebuffer, then one warp onto the projector.")

    X, CW = 90, 700
    rows = [
        (150, "1", "table background", "#E8E6E1, a warm near-white, cleared first", PANEL, INK),
        (250, "2", "the fluid", "the fire, drawn under MULTIPLY so it DARKENS the table", "#f6e6dd", ACCENT),
        (350, "4", "the halo", "a golden ring wrapped around each bin", "#fdf6dd", "#a5761b"),
        (450, "5", "the UI", "plate names, prices, the info box, the cart, the buttons", PANEL, INK),
        (570, "3", "THE LIGHT PASS", "flat 255,255,255 over every tray cutout, opaque, corners rounded", "#ffffff", COOL),
    ]
    for y, num, name, note, fill, stroke in rows:
        h = 84
        s.rect(X, y, CW, h, fill=fill, stroke=stroke, sw=2.6, rx=8)
        s.circle(X + 46, y + h / 2, 24, fill=stroke, stroke=stroke)
        s.text(X + 46, y + h / 2 + 9, num, 24, "#ffffff", anchor="middle",
               weight="bold")
        s.text(X + 90, y + 36, name, 23, stroke, weight="bold")
        s.text(X + 90, y + 64, note, 17, MUTED)

    s.text(X + CW + 30, 194, "numbered bottom to top,", 17, MUTED)
    s.text(X + CW + 30, 218, "the way the eye reads them", 17, MUTED)

    s.path(f"M {X + CW + 16} 492 C {X + CW + 120} 492, {X + CW + 120} 600, {X + CW + 16} 606",
           stroke=COOL, sw=2.6, arrow="ac")
    s.caption(X + CW + 34, 530, [
        "layer 3 is drawn",
        "structurally LAST",
    ], 19, COOL, gap=26)

    s.line(X + CW / 2, 654, X + CW / 2, 700, arrow="a", sw=2.6)
    s.box(X, 702, CW, 78, ["keystone warp, then the window"],
          head_size=22, fill=COOL_FILL, stroke=COOL, head_fill=COOL)

    s.save("architecture-light-pass.svg")


# -------------------------------------------------------------- 10. stage layout
def stage():
    W, H = 1220, 845
    s = Svg(W, H, "The 440 mm centre column, in pixels",
            "Stage space is 1920 x 1080. Every band below is derived from the bin chain, so moving a bin moves them all.")

    K = 0.55
    OX, OY = 96, 140

    def px(x):
        return OX + x * K

    def py(y):
        return OY + y * K

    s.rect(px(0), py(0), 1920 * K, 1080 * K, fill="#faf7f2", stroke=INK, sw=2.4)

    bins = [(115.9, 209.1), (430.9, 209.1), (1237.2, 209.1), (1552.1, 209.1),
            (115.9, 569.3), (430.9, 569.3), (1237.2, 569.3), (1552.1, 569.3)]
    for i, (bx, by) in enumerate(bins):
        s.rect(px(bx), py(by), 252.0 * K, 301.2 * K, fill="#ffffff",
               stroke=ACCENT, sw=2.2, rx=6)
        s.text(px(bx + 126), py(by + 150) + 9, str(i), 26, ACCENT,
               anchor="middle", weight="bold")

    COL_L, COL_W = 682.8, 554.3

    def band(y, h, colour, fill, label, sub=None, size=18):
        s.rect(px(COL_L), py(y), COL_W * K, h * K, fill=fill, stroke=colour,
               sw=2.0, rx=5)
        cx = px(COL_L + COL_W / 2)
        if sub:
            s.text(cx, py(y + h / 2) - 2, label, size, colour, anchor="middle",
                   weight="bold")
            s.text(cx, py(y + h / 2) + 18, sub, 15, MUTED, anchor="middle")
        else:
            s.text(cx, py(y + h / 2) + 6, label, size, colour, anchor="middle",
                   weight="bold")

    band(20, 170, "#a5761b", "#fdf6dd", "brand mark", "20 to 190")
    s.rect(px(COL_L), py(216), COL_W * K, 278.5 * K, fill="#eef6f9",
           stroke=COOL, sw=2.0, rx=5)
    s.text(px(COL_L + COL_W / 2), py(430), "info box", 18, COOL,
           anchor="middle", weight="bold")
    s.text(px(COL_L + COL_W / 2), py(462), "216 to 494", 15, MUTED,
           anchor="middle")
    s.rect(px(COL_L), py(216), COL_W * K, 104 * K, fill=BAD_FILL,
           stroke=BAD, sw=2.2, rx=5, dash="7 5")
    s.text(px(COL_L + COL_W / 2), py(258) + 2, "mode banner", 18, BAD,
           anchor="middle", weight="bold")
    s.text(px(COL_L + COL_W / 2), py(258) + 24, "216 to 320, instead of the box",
           15, BAD, anchor="middle")
    band(506.5, 256, ACCENT, "#fdf1e6", "the cart, 8 rows of 32 px",
         "506 to 762")
    band(762.5, 108, ACCENT, "#fdf1e6", "total", "762 to 870")
    band(937.2, 76, GOOD, GOOD_FILL, "three button slots", "937 to 1013")

    # column measure
    ym = py(1080) + 40
    s.line(px(COL_L), ym, px(COL_L + COL_W), ym, stroke=INK, sw=1.8)
    s.line(px(COL_L), ym - 7, px(COL_L), ym + 7, stroke=INK, sw=1.8)
    s.line(px(COL_L + COL_W), ym - 7, px(COL_L + COL_W), ym + 7, stroke=INK,
           sw=1.8)
    s.text(px(COL_L + COL_W / 2), ym + 30, "554 px  =  440 mm", 19, INK,
           anchor="middle", weight="bold")

    s.text(px(0), py(0) - 16, "0, 0", 16, MUTED)
    s.text(px(1920), py(0) - 16, "1920", 16, MUTED, anchor="end")
    s.text(px(1920) + 12, py(1080), "1080", 16, MUTED)

    s.save("architecture-centre-column.svg")


# ------------------------------------------------------------ 11. tracker window
def tracker():
    W, H = 1240, 775
    s = Svg(W, H, "One hand, one window, seven hundred pixels",
            "MediaPipe's tracking state is bound to the exact framing it was given, and that decided this whole mechanism.")

    K = 0.44
    OX, OY = 110, 150

    def px(x):
        return OX + x * K

    def py(y):
        return OY + y * K

    s.rect(px(0), py(0), 1920 * K, 1080 * K, fill="#faf7f2", stroke=INK, sw=2.4)
    s.text(px(0), py(0) - 16, "the camera frame, 1920 x 1080", 18, MUTED)

    s.rect(px(110), py(40), 1700 * K, 1000 * K, fill="#ffffff", stroke=RULE,
           sw=2.0, dash="8 6")

    for x in (150, 620, 1090):
        for y in (60, 320):
            s.rect(px(x), py(y), 700 * K, 700 * K, fill="none", stroke=COOL,
                   sw=1.6, dash="5 5")

    hx, hy = 980, 620
    s.rect(px(hx - 350), py(hy - 350), 700 * K, 700 * K, fill="#fff3e8",
           stroke=ACCENT, sw=3.0)
    s.circle(px(hx), py(hy), 13, fill=ACCENT, stroke=ACCENT)
    s.text(px(hx) + 24, py(hy) + 7, "landmark 8, the index fingertip", 17,
           ACCENT, weight="bold")

    ly = py(1080) + 46
    keys = [(RULE, "8 6", "the table's own footprint, padded by 200 px on every side"),
            (COOL, "5 5", "scan tiles: 700 px wide, 470 px apart, so they overlap by a third"),
            (ACCENT, None, "a committed window: 700 px, re-centred on its own last hit every tick")]
    for i, (col, dash, label) in enumerate(keys):
        y = ly + i * 32
        s.line(px(0), y - 6, px(0) + 54, y - 6, stroke=col,
               sw=3.0 if dash is None else 2.2, dash=dash)
        s.text(px(0) + 70, y, label, 18, INK)

    s.save("architecture-hand-window.svg")


# ---------------------------------------------------------------- 12. the shaders
def shaders():
    W, H = 1280, 1085
    s = Svg(W, H, "Two shaders and one resolution pair",
            "The fire's density field is 1280 x 720. Its simulation grid is 640 x 360. Both shaders read across that gap.")

    s.text(60, 150, "ftBuoyancyShader: the weight term came from half the coordinates",
           22, INK, weight="bold")

    K = 0.42
    SX, SY = 110, 280
    DX, DY = 560, 280

    s.text(SX, SY - 40, "the shader renders here", 20, INK, weight="bold")
    s.text(SX, SY - 18, "the sim grid, 640 x 360", 16, MUTED)
    s.rect(SX, SY, 640 * K, 360 * K, fill=PANEL, stroke=INK, sw=2.4)

    s.text(DX, DY - 40, "and samples this", 20, ACCENT, weight="bold")
    s.text(DX, DY - 18, "the density field, 1280 x 720", 16, MUTED)
    s.rect(DX, DY, 1280 * K, 720 * K, fill="#fdf1e6", stroke=ACCENT, sw=2.4)
    s.rect(DX, DY, 640 * K, 360 * K, fill="none", stroke=BAD, sw=1.8,
           dash="7 5")

    fx, fy = SX + 400 * K, SY + 200 * K
    ux, uy = DX + 400 * K, DY + 200 * K
    gx, gy = DX + 800 * K, DY + 400 * K

    s.path(f"M {fx + 10} {fy - 6} C {fx + 140} {fy - 130}, {ux - 140} {uy - 130}, {ux - 10} {uy - 6}",
           stroke=BAD, sw=2.4, arrow="ab")
    s.path(f"M {fx + 8} {fy + 10} C {fx + 180} {fy + 260}, {gx - 240} {gy + 130}, {gx - 10} {gy + 8}",
           stroke=GOOD, sw=2.4, arrow="a")

    s.circle(fx, fy, 10, fill=INK, stroke=INK)
    s.circle(ux, uy, 10, fill=BAD, stroke=BAD)
    s.circle(gx, gy, 10, fill=GOOD, stroke=GOOD)
    s.text(fx - 16, fy + 40, "st = (400, 200)", 17, INK, anchor="middle",
           weight="bold", font=MONO)

    ly = DY + 720 * K + 40
    s.circle(DX + 10, ly - 6, 9, fill=BAD, stroke=BAD)
    s.text(DX + 30, ly, "upstream reads here:  texture2DRect(tex_density, st)",
           18, BAD, weight="bold", font=MONO)
    s.circle(DX + 10, ly + 30, 9, fill=GOOD, stroke=GOOD)
    s.text(DX + 30, ly + 36, "the patch reads here:  st * densityScale,  which is (2, 2)",
           18, GOOD, weight="bold", font=MONO)
    s.text(SX, ly, "The dashed square is the only", 17, BAD)
    s.text(SX, ly + 22, "part of the density field the", 17, BAD)
    s.text(SX, ly + 44, "unscaled read can ever reach.", 17, BAD)

    s.text(110, 700, "buoyancy_force = timestep * dtemp * fluid_buoyancy - density * fluid_weight",
           18, INK, weight="bold", font=MONO)

    s.text(60, 800, "ftJacobiDiffusionShader: the obstacle lookup clamped, and wiped the canvas",
           22, INK, weight="bold")

    cy = 850
    s.box(110, cy, 480, 200,
          ["the GLSL 4.10 variant",
           "",
           "vec2 st2 = st * scale;",
           "texture(tex_obstacle, st2)",
           "",
           "correct, upstream, dead code here"],
          head_size=21, body_size=18, body_fill=INK, top=cy + 44, fill=PANEL)
    s.box(620, cy, 480, 200,
          ["the GLSL 1.20 variant",
           "",
           "texture2DRect(tex_obstacle, st)",
           "",
           "the raw coordinate, no scale,",
           "and this is the one that runs"],
          head_size=21, body_size=18, body_fill=BAD, top=cy + 44,
          fill=BAD_FILL, stroke=BAD, head_fill=BAD)

    s.save("architecture-shader-bugs.svg")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

DIAGRAMS = {
    "processes":        (processes, "architecture-processes.svg"),
    "cursor-drain":     (drain,     "architecture-cursor-drain.svg"),
    "frame-ring":       (ring,      "architecture-frame-ring.svg"),
    "coordinate-spaces": (spaces,   "architecture-coordinate-spaces.svg"),
    "two-grids":        (grids,     "architecture-two-grids.svg"),
    "state-machine":    (fsm,       "architecture-state-machine.svg"),
    "cart-weights":     (weights,   "architecture-cart-weights.svg"),
    "scale-filter":     (scalefilter, "architecture-scale-filter.svg"),
    "light-pass":       (fbo,       "architecture-light-pass.svg"),
    "centre-column":    (stage,     "architecture-centre-column.svg"),
    "hand-window":      (tracker,   "architecture-hand-window.svg"),
    "shader-bugs":      (shaders,   "architecture-shader-bugs.svg"),
}


def rasterise(svg_name: str) -> None:
    """SVG to PNG via a headless Chromium. The size is read back out of the
    file's own width/height so the PNG is always 1:1 with the drawing.
    """
    svg = OUT / svg_name
    head = svg.read_text(encoding="utf-8")[:400]
    m = re.search(r'width="(\d+)" height="(\d+)"', head)
    if not m:
        print(f"  no width/height in {svg_name}, skipping the png")
        return
    browser = next((b for b in BROWSERS if Path(b).exists()), None)
    if browser is None:
        print("  no Chrome or Edge found, skipping the png "
              "(set HOTPOT_BROWSER)")
        return
    png = svg.with_suffix(".png")
    with tempfile.TemporaryDirectory() as tmp:
        wrap = Path(tmp) / "wrap.html"
        wrap.write_text(
            '<html><body style="margin:0">'
            f'<img src="{svg.as_uri()}" style="width:100vw;display:block">'
            "</body></html>", encoding="utf-8")
        # --user-data-dir forces a standalone instance: without it this rig's
        # Edge hands the job to the running browser and screenshots nothing,
        # or its "file not found" page.
        subprocess.run(
            [browser, "--headless", "--disable-gpu", "--no-first-run",
             "--hide-scrollbars", f"--user-data-dir={Path(tmp) / 'prof'}",
             f"--screenshot={png}",
             f"--window-size={m.group(1)},{m.group(2)}",
             "--default-background-color=FFFFFFFF", wrap.as_uri()],
            capture_output=True)
    ok = png.exists() and png.stat().st_size > 0
    print(f"       {png.relative_to(ROOT)}" + ("" if ok else "  (FAILED)"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="render_diagrams.py",
        description="Render the Tier 3 architecture diagrams.")
    p.add_argument("names", nargs="*", help="which ones (default: all)")
    p.add_argument("--list", action="store_true", help="print the names")
    p.add_argument("--no-png", action="store_true",
                   help="write the SVG only")
    args = p.parse_args(argv)

    if args.list:
        for name in DIAGRAMS:
            print(name)
        return 0

    names = args.names or list(DIAGRAMS)
    unknown = [n for n in names if n not in DIAGRAMS]
    if unknown:
        raise SystemExit(
            f"render_diagrams.py: unknown diagram(s): "
            f"{', '.join(unknown)}. Choices: {', '.join(DIAGRAMS)}")

    for name in names:
        fn, svg_name = DIAGRAMS[name]
        fn()
        if not args.no_png:
            rasterise(svg_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
