# #210 patch canvas — every setting, captured

Reference frames for the settings decision 57 §5 says will ship in the Settings
window (#213, reached from the menu bar in #214). They exist so #211 can be
built, and later argued with, without re-running the prototype or re-reading
this conversation.

Regenerate all of them after any change to the prototype:

```
python scripts/htmlshot.py docs/prototypes/210-patch-canvas.html \
  --batch docs/screenshots/210/states.json --outdir docs/screenshots/210 \
  --clip ".app" --width 1860 --height 1000 --viewport-only
```

`states.json` holds the JS that drives each state, so a new setting needs one
entry there and nothing else. Each frame is cropped to the app window — the
prototype's control rail is deliberately outside the crop, since it is a
stand-in for the Settings window rather than part of the canvas.

## The frames

| File | Shows |
|---|---|
| `00-default.png` | The shipping defaults: stripe, bézier, colour by meaning, sag 0.55, swing 0.70, resting brightness 0.28, loop cables glowing. |
| **Where the per-note / once-only line goes** — settled, not a setting | |
| `01-mix-big-stripe.png` | **Settled.** Labelled stripe across the canvas, tinted halves, area labels. Massive X's treatment. |
| `02-mix-small-box.png` | Rejected. MIX as an ordinary module. The boundary exists only where cables meet it — compare how much harder it is to see which side anything is on. |
| `03-mix-box-plus-tint.png` | Rejected. The middle option: discrete box, tinted halves, no physical divider. |
| **Cable sag** | |
| `04-sag-none.png` | Slider at 0. Cables are near-straight; they read as drawn lines rather than objects. |
| `05-sag-max.png` | Slider at 1. Deep hang. Legibility cost is visible in the dense left-hand group. |
| **Resting brightness** | |
| `06-brightness-faintest.png` | 0.06. Cables almost gone; the knobs are perfectly readable and the patch is not. |
| `07-brightness-full.png` | 1.00. What the canvas looked like before this setting existed — cables win, knobs lose. |
| **Cable colours** | |
| `08-colour-by-meaning.png` | Default. Three hues: per-note sound, once-only sound, knob movement. |
| `09-colour-all-different.png` | VCV's cycling five, in VCV's actual colours, deliberately unretouched against Copper. |
| **Marking the feedback loop** | |
| `10-loop-unmarked.png` | Nothing. Does CHORUS → DELAY read as a cycle on its own? |
| `11-loop-glow-cables.png` | Default. The two cables in the cycle glow copper. |
| `12-loop-glow-modules-too.png` | Reaktor's treatment: the modules in the cycle get the copper border as well. |
| **Where a refusal explains itself** | |
| `13-refusal-at-the-socket.png` | Default. Callout beside the refused input — note it covers DELAY's knobs. |
| `14-refusal-bottom-bar.png` | Status bar only. Nothing is covered; the message is far from where you are looking. |
| `15-refusal-both.png` | Both at once. |
| **Behaviour, not settings** | |
| `16-module-selected-cables-lit.png` | FILTER selected: its four audio cables and both modulation cables at full brightness, the ring lit on its Cutoff knob, everything else faint. This is the resting-brightness rule doing its job. |
| `17-lfo-per-note.png` | LFO 1 on `note`, left of the line, sixteen out-of-phase copies. |
| `18-lfo-global.png` | LFO 1 on `glob`: it crosses to the once-only side and may now grab a once-only knob (decision 56 §5). |

## What a still cannot show

**Swing.** The `bounce` setting only exists in motion — a still frame catches
the cables at rest either way. Drag a module in the live prototype to judge it;
there is no captured frame for it and there should not be a misleading one.
