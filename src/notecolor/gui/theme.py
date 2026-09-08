"""VisualNote Studio's visual language.

One rule governs everything here, and it is *semantic* rather than a matter of
taste:

    **Saturation means pitch. Nothing else in the interface gets a strong
    colour.**

Every chrome surface, label, border and button is drawn from a muted ink
palette. The only saturated things on screen are notes, and the hue they carry
is this app's own circle-of-fifths mapping -- the same one `tab`, the score
writer and the synth keys already use, so a C is the same colour everywhere.
That makes colour *information* rather than decoration: if something is vivid,
it is telling you a pitch.

The palette is **Kanagawa** -- the Hokusai-derived ink-wash scheme (sumi ink
grounds, fuji white text, muted autumn accents). It suits this because it is
already built around that restraint, so note hues sit on top without competing.

The typeface is **JetBrains Mono Nerd Font** everywhere, labels included. That
is structural, not nostalgic: a DAW is a dense grid of numbers and names,
monospace makes columns line up without measuring, and this project's other
front-end is a terminal -- the two should look like the same program.

Surfaces are painted with alpha so the compositor shows through. Square
corners, hairline rules, no gradients, no shadows, no rounded blobs.
"""

from PySide6 import QtGui

# --- Kanagawa --------------------------------------------------------------

SUMI_INK_0 = "#16161D"
SUMI_INK_1 = "#1F1F28"
SUMI_INK_2 = "#2A2A37"
SUMI_INK_3 = "#363646"
SUMI_INK_4 = "#54546D"
FUJI_WHITE = "#DCD7BA"
OLD_WHITE = "#C8C093"
FUJI_GRAY = "#727169"
KATANA_GRAY = "#717C7C"
WAVE_BLUE_2 = "#2D4F67"
AUTUMN_RED = "#C34043"
DRAGON_BLUE = "#658594"
CARP_YELLOW = "#E6C384"

#: How much desktop shows through. Chrome is more opaque than the canvas --
#: text has to stay readable over whatever happens to be behind the window.
CANVAS_ALPHA = 196
CHROME_ALPHA = 224
PANEL_ALPHA = 208

FONT_FAMILY = "JetBrainsMono Nerd Font Mono"


def ink(hex_colour, alpha=255):
    colour = QtGui.QColor(hex_colour)
    colour.setAlpha(alpha)
    return colour


def font(size=9, bold=False):
    face = QtGui.QFont(FONT_FAMILY, size)
    face.setStyleHint(QtGui.QFont.Monospace)
    face.setWeight(QtGui.QFont.DemiBold if bold else QtGui.QFont.Normal)
    return face


# --- semantic roles, so no widget reaches for a raw hex value --------------

CANVAS = ink(SUMI_INK_1, CANVAS_ALPHA)
LANE = ink(SUMI_INK_1, CANVAS_ALPHA)
LANE_ALT = ink(SUMI_INK_0, CANVAS_ALPHA)
CHROME = ink(SUMI_INK_2, CHROME_ALPHA)
CHROME_DEEP = ink(SUMI_INK_0, CHROME_ALPHA)
PANEL = ink(SUMI_INK_2, PANEL_ALPHA)
RULE = ink(SUMI_INK_3)
RULE_STRONG = ink(SUMI_INK_4)
TEXT = ink(FUJI_WHITE)
TEXT_DIM = ink(FUJI_GRAY)
TEXT_FAINT = ink(SUMI_INK_4)
CLIP_BODY = ink(SUMI_INK_2, 232)
CLIP_HEAD = ink(SUMI_INK_3, 240)
CLIP_EDGE = ink(SUMI_INK_4)
#: The playhead is deliberately *not* a strong colour: bone white, one pixel.
#: It reads by contrast and motion rather than by shouting.
PLAYHEAD = ink(FUJI_WHITE, 230)
#: The single muted accent that is not pitch: record-armed. Autumn red, not a
#: signal red, so it can never outrank a note on screen.
ARMED = ink(AUTUMN_RED, 210)
SELECTION = ink(WAVE_BLUE_2, 150)
