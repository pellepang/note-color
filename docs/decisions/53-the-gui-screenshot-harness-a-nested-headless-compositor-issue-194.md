# The GUI screenshot harness: a nested headless compositor (issue #194)

Phase 0 of the Synth View map (#191). Everything in that map is cheaper
once an agent can see what it just changed.

## The problem this solves

Every GUI ticket on this project ended the same way: an agent changed
layout code, could not see the result, handed it over, and the user found
the obvious defect immediately. #164/#168 (clipped module names) went
round that loop twice and are on their third attempt. The user could not
usefully screenshot either -- on Hyprland the app tiles into whatever
they are doing, so the shot shows a window at the wrong size next to
their editor.

The cost was never the individual bug. It was that the feedback loop ran
through a human's eyes at the *end* of a change instead of the agent's at
the start of one.

## What was built

`scripts/uishot.py` (orchestrator) + `scripts/ui_states.py` (the named
states). One command, one PNG:

    .venv/bin/python scripts/uishot.py synth-view -o /tmp/synth.png

The orchestrator starts a headless `sway` on a private `WAYLAND_DISPLAY`
with a virtual output at the user's *real* resolution and scale (read from
their running Hyprland, overridable by flag), runs one named UI state
inside it, waits for the state to report that it has actually painted, and
captures with `grim` against that nested display. Teardown is a context
manager, so an exception, a timeout or a Ctrl-C all take the compositor
with them -- a stray compositor is the one way this could damage a session
it promises not to touch.

A "named UI state" is a zero-argument function returning the top-level
widget to shoot, plus a dict entry. That is deliberately the whole
extension surface: a later ticket says "screenshot `drawer-expanded`",
gets the same frame every time, and adds a new state in six lines.

Two details that are load-bearing rather than incidental:

* The states build their `QApplication` through
  `notecolor.gui.app.build_app()`, the same call the real app makes, so
  the font and app id are the real ones. A harness that configures its own
  application screenshots a program that does not exist.
* The runner prints `READY` only after the window has painted a frame, so
  the capture happens on a signal rather than a guessed sleep. Guessed
  sleeps are how a harness starts silently shooting half-drawn frames six
  months later.

No state opens audio. The controller is a stub with no `SoundEngine`,
mirroring `tests/test_synth_view.py` -- the headless compositor has no
sound device, and a view that needed one in order to *render* would be a
bug in the view.

## Rejected

**Screenshotting into the user's live session.** The window tiles into
whatever they are doing; the shot is worthless and their workspace is
disturbed. The user's own report.

**`QT_QPA_PLATFORM=offscreen` alone.** No compositor, and it lies about
fonts and DPI. That is precisely the class of defect the harness exists to
catch: a screenshot rendered at the wrong metrics cannot answer "does this
label clip", which is the question three open tickets are asking.

**Hyprland as the nested compositor**, even though it is what the user
runs and what #194 offered as an alternative to sway. It cannot do this.
0.56 picks its backend from the environment -- the wayland backend if
`WAYLAND_DISPLAY` is set, DRM otherwise -- and exposes no `AQ_BACKENDS`,
no `WLR_BACKENDS`, and no headless-only switch (checked against both the
binary's and aquamarine 0.15's symbol tables). With the parent display
unset and DRM starved through `AQ_DRM_DEVICES`, `CBackend::create()` fails
outright rather than falling back to headless. Nested under the user's
session it comes up on the *wayland* backend and opens a real window in
their session -- the exact thing the harness must not do. Verified on
0.56.2, 2026-09-12. Hence sway, which #194 named first anyway, and which
is a build-tooling dependency only: nothing shipped imports it.

## The app id

The app set no `WM_CLASS`/`app_id` at all, so no Hyprland `windowrulev2`
could target it -- needed by this harness and by the detach work in #192.
Qt derives both from `QGuiApplication.setDesktopFileName()`, now called
with `notecolor.gui.app.APP_ID`.

`APP_ID` is **`visualnote`, not `notecolor`** as #194's example wrote it.
The example was written before anyone checked that
`packaging/visualnote.desktop` already existed. Matching the installed
desktop entry is what makes the id mean anything beyond a string a window
rule agrees with: it is what the icon lookup and the xdg-desktop portal's
app registration key off, and with the old value the portal logged
`Could not register app ID: App info not found for 'notecolor'` on every
launch. `SYNTH_APP_ID = "visualnote-synth"` is reserved, unused, for the
detached Synth View window (#191 decision 5), so a user can write the rule
for it before the feature lands.

## The standing limit

#191's decision 8, restated because it is the thing most likely to erode:
**the harness is a pre-handover self-check, never proof.** An agent does
not declare a GUI fix correct from its own headless screenshot. Taste goes
to the user, batched at the end of a change, each question paired with the
shot of the exact state it is asking about and phrased so "no" is easy.

The harness makes the agent's *own* obvious mistakes cheap to catch. It
does not move the verdict.
