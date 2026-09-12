# 57. The patch canvas's settled look: cables with weight, and a labelled stripe (issue #210)

Decision 56 §9 said the Synth View's look would be settled by **HTML prototypes
and conversation, before any Qt work**. This is that settling. Three rounds of
prototype (`docs/prototypes/210-patch-canvas.html`, commits `a977f57`,
`c2fb334`) plus the owner's direction over them, 2026-09-12.

It is worth recording that the round-1 prototype asked the wrong shape of
question. It offered six either/or choices in the vocabulary of the prior-art
survey — Massive X's Polyphonic Area, VCV's tension slider, Reason's graded hide
modes — and got back:

> "i do not understand youre questions so ill just give you mi input"

The input that followed was better than anything the questions would have
extracted, and most of it was not on the menu. Two lessons are baked into the
decisions below: **ask about what changes on screen, not about which product's
convention to adopt**, and **look-and-feel variants are usually settings, not
forks**.

## 1. The Mix boundary is a labelled stripe across the canvas

Massive X's treatment, chosen over a discrete Mix module in the middle:

> "i like the stripe for now. this is what i want it to look like."

Everything left of the stripe runs once per held note; everything right of it
runs once (decision 56 §3). The stripe costs ~150px of canvas permanently,
which is exactly why this stayed a design call rather than becoming a setting
like everything else in §5 — a switch that changes the canvas's usable width is
not personalisation.

The discrete-box and box-plus-tint treatments stay live in the prototype as a
record of what was compared. They are not shipping.

## 2. Cables are objects with weight

The owner's words:

> "i want the cable to have gravity, and move when the moduals are moved, so
> that they feel like they have phisics."

Not decoration, and not a polish item to defer. A cable hangs under its own
weight, and when a module moves the ends jump while the middle lags, overshoots
and settles.

The prototype's mechanism, which #211 should port rather than reinvent: **one
particle per cable, at its midpoint**. Each frame it is pulled toward a rest
point that sits below the straight line between the two jacks, with a constant
downward term and damping. The drawn curve is a quadratic Bézier whose control
point is placed so the curve passes through the particle. Swing therefore falls
out of the ends moving; there is no separate animation to trigger.

**Bézier is the shape.** Straight and orthogonal routing were offered in round 1
and are gone — they cannot express weight, and the owner asked for bézier by
name as the default. They are not planned.

## 3. Cables rest faint and light up on touch

> "i whant the cables to be less viseble if not selected, when routing the
> cabels they should be on top like now but less visible when not selected so
> it is easier to see the knobs."

One behaviour, not a set of modes. A cable is dim at rest — dim enough to read
the knobs through — and goes to full brightness when the user hovers the cable,
hovers one of its jacks, selects its module, or turns a knob it feeds. Cables
stay drawn **above** the module windows throughout, including while routing.

This replaces round 1's four graded hide modes (Reason's, including the
cables-off/coloured-dots-in-the-jacks mode) and the dim-unselected mode. The
owner answered the clutter question differently from every option offered, and
the answer supersedes the menu. The dots-in-the-jacks idea survives only as the
pip that every connected jack already carries.

**Modulation is the same rule with a tighter trigger.** A modulation cable and
the dashed ring on its destination knob brighten *while that knob is being
turned*, and fade back when it is not:

> "the red dotted lines show the contections with the knobs the connection
> should be more visible if turning the knob and less visible when not."

## 4. Sockets grow one at a time

> "i also whant them to have more imputs and outputs. they should always have
> one more that the used amount so that every cable has its own imput or output
> location and the always one more so that i can wire up one more cable if
> needed but its not clutterd with imputs and outputs if not used."

A module declares no fixed socket list. It shows **exactly as many jacks as it
has cables, plus one spare**, on each side it accepts connections. The spare is
drawn as a dashed empty hole so it reads as available rather than as an unused
jack. Plug into it and a new spare appears; unplug and the row compacts.

Two consequences worth stating:

- **Every cable owns a jack.** Nothing sums invisibly at a shared input, which
  is the same instinct decision 56 §3 acted on when it refused silent
  auto-summing at the poly boundary.
- **The Delay's separate "feedback in" disappears.** With one jack per cable
  there is nothing for a dedicated feedback input to distinguish; the return
  cable simply takes the next In.

## 5. Every appearance value is a setting, not a fork

The prototype's control rail was built as throwaway scaffolding for choosing
between variants. The owner's answer:

> "i think that it all should be implemented as options in the settings ...
> this is great for personalization of the look and feel of the interface it
> should all be implemented and be able to change in the settings that we later
> will have to make"

So the rail became the first spec for a Settings window the app does not have.
Shipping as settings: cable sag, cable swing, resting brightness, cable colour
scheme, feedback-loop marking, and where a refusal's explanation appears.

**The defaults #211 ships with**, explicitly chosen by the owner rather than
inherited: **bézier cables**, **colour by meaning** (per-note sound / once-only
sound / knob movement, three hues), **cables drawn on top**. Sag, swing and
resting brightness ship at the prototype's values (0.55 / 0.70 / 0.28), which
are guesses and should be re-judged against the real Qt canvas.

#211 does not have to build the Settings window, but it must keep these six as
named values in one place so wiring them up later is mechanical rather than a
hunt through scattered literals.

## 6. Settings are reached from a menu bar, not from the main window

> "the settings should not be placed on the main window it be accest from the
> header with file edit view and transport. their should be another option
> called settings"

The Synth View's top bar is a patch/engine/transport strip, not a menu bar, and
the app has no `QMenuBar` at all today. One is now required work: **File, Edit,
View, Transport, Settings**. The rail in the prototype is a preview of the
Settings window's content, not a proposal to dock a panel to the canvas.

## What this obsoletes

- Round 1's four clutter modes and the dim-unselected mode (§3 replaces them).
- Straight and orthogonal cable routing (§2).
- Fixed per-module socket declarations, and the Delay's dedicated feedback
  input (§4).
- Framing the appearance controls as choices to settle before #211 (§5).

## Tickets

- **#210** — the prototype; the owner's direction is recorded in its comments.
- **#211** — the Qt canvas. Carries §1-§4 as requirements and §5's defaults.
- **#213** — the settings themselves, as listed in §5.
- **#214** — the menu bar (§6) and the Settings window's own look, prototype
  first, the same way this canvas was designed.
