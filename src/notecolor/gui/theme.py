"""VisualNote Studio's visual language.

One rule governs everything, and it is *semantic* rather than a matter of
taste:

    **Saturation means pitch. Nothing else in the interface gets a strong
    colour.**

Every surface, rule, label and button is muted. The only saturated things on
screen are notes, carrying this app's own circle-of-fifths hue -- the same one
`tab`, the score writer and the synth keys already use, so a C is the same
colour everywhere. Colour is information, not decoration: if something is
vivid, it is telling you a pitch.

The palette is **Copper** (MrPanda376's VS Code theme), taken from its own
source rather than eyeballed from a screenshot -- deep charcoal browns, copper
accents, soft amber, muted teal.

Copper's signature accent is a genuinely strong orange, which sits in tension
with the rule above, so it is confined to **hairline scale**: a one-pixel
playhead and a 14-pixel record letter. It never fills an area. That keeps the
app's identity without letting chrome compete with a note.

The typeface is **JetBrains Mono Nerd Font** everywhere, labels included. That
is structural rather than nostalgic: a DAW is a dense grid of numbers and
names, monospace aligns columns without measuring, and this project's other
front-end is a terminal -- the two should look like one program.

Surfaces are painted with alpha so the compositor shows through. Square
corners, hairline rules, no gradients, no shadows, no rounded blobs.
"""

from PySide6 import QtGui

# --- Copper ---------------------------------------------------------------
# github.com/MrPanda376/copper-vscode-theme -> themes/copper-color-theme.json

INK_0 = "#16100E"         # deeper than the theme's own floor, for wells
INK_1 = "#1A1412"         # editor.background
INK_2 = "#221A17"         # sideBar / statusBar / titleBar
INK_3 = "#2A211D"         # tab.active / input
RULE_1 = "#3D2F27"        # editorGroup.border, indent guides
RULE_2 = "#4A3428"        # list.activeSelection
LINEN = "#E8D5C4"         # foreground
LINEN_DIM = "#C4AA96"     # sideBar.foreground
LINEN_FAINT = "#8A7565"   # editorLineNumber.foreground
EMBER = "#6B5647"         # comments -- the faintest legible tone

COPPER = "#FF7034"        # focusBorder / button / badge -- signature accent
COPPER_LIGHT = "#FF8F5F"  # editorCursor
AMBER = "#F0A855"
CORAL = "#F77C4F"
TEAL = "#5A8A9A"          # keywords
TEAL_PALE = "#9BB3B8"     # variables
CLAY_RED = "#E65C4F"      # errorForeground

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

CANVAS = ink(INK_1, CANVAS_ALPHA)
LANE = ink(INK_1, CANVAS_ALPHA)
LANE_ALT = ink(INK_0, CANVAS_ALPHA)
CHROME = ink(INK_2, CHROME_ALPHA)
CHROME_DEEP = ink(INK_0, CHROME_ALPHA)
PANEL = ink(INK_3, PANEL_ALPHA)
RULE = ink(RULE_1)
RULE_STRONG = ink(RULE_2)
TEXT = ink(LINEN)
TEXT_DIM = ink(LINEN_DIM)
TEXT_FAINT = ink(EMBER)
CLIP_BODY = ink(INK_2, 236)
CLIP_HEAD = ink(INK_3, 244)
CLIP_EDGE = ink(RULE_2)

#: The two places the accent is allowed, both hairline-scale rather than an
#: area of colour: the playhead and record-armed.
PLAYHEAD = ink(COPPER, 235)
ARMED = ink(CLAY_RED, 225)
FOCUS = ink(COPPER, 180)
SELECTION = ink(RULE_2, 170)


def rgba(colour):
    """A Qt stylesheet colour string for one of the roles above."""
    return f"rgba({colour.red()},{colour.green()},{colour.blue()},{colour.alpha() / 255:.3f})"


#: The arrange view's own viewport. Separate from `main_stylesheet()` because
#: it is applied to one widget rather than the window, but it lives here for
#: the same reason everything else does: chrome styling is decided in this
#: module, not scattered through the widgets.
CANVAS_STYLESHEET = "background: transparent; border: 0;"


def main_stylesheet():
    """Chrome Qt draws for us -- docks, splitters, scrollbars, labels.

    Lives here rather than in a window, so that every colour in the interface
    comes from a named role in this module. A widget that reaches for a raw
    hex value is how a palette quietly stops being a palette.
    """
    return f"""
        QMainWindow, QWidget {{ background: transparent; }}
        QLabel {{ color: {rgba(TEXT_DIM)}; background: {rgba(PANEL)}; padding: 8px; }}
        QDockWidget {{ color: {rgba(TEXT_DIM)}; font-family: "{FONT_FAMILY}";
                       font-size: 10px; }}
        QDockWidget::title {{ background: {rgba(CHROME_DEEP)}; padding: 4px 8px;
                              text-align: left;
                              border-bottom: 1px solid {rgba(RULE)}; }}
        QSplitter::handle {{ background: {rgba(RULE_STRONG)}; height: 1px; }}
        QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
        QScrollBar::handle:horizontal {{ background: {rgba(RULE_STRONG)};
                                         min-width: 40px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    """
