# Computer keyboard as a piano: rollover limits, this machine, and what other software does

Research for map [#226](https://github.com/pellepang/note-color/issues/226), filed after
[#221](https://github.com/pellepang/note-color/issues/221) (Synth View polyphony "feels capped
near 5 voices, and which notes survive varies") cleared the 16-voice engine completely --
deterministic, zero collisions, 16 `note_on()` calls make 16 voices every time. The remaining
suspect was the keyboard itself. Related: [#173](https://github.com/pellepang/note-color/issues/173)
(no MIDI hardware input; QWERTY notes have no velocity/sustain/mod-wheel).

Target machine: a Lenovo ThinkPad T480, two keyboards present --
`AT Translated Set 2 keyboard` (the built-in laptop keyboard, PS/2-style via the `i8042`/`atkbd`
driver) and a `Telink Wireless Receiver` (a wireless keyboard+mouse combo on a USB dongle,
2.4 GHz, HID over USB). Sources are cited inline; where I could not verify something from a
primary source, or could not measure something myself, it says so plainly -- see also
"What I could not verify / could not do" at the end.

---

## 1. The general limit -- three ceilings, not one

The owner's report ("about five notes, and *which* notes varies") is the classic signature of a
matrix problem, not a flat cap, and the reason is that "how many keys can this keyboard report at
once" is actually three independent questions stacked on top of each other. Each has a different
owner and a different fix.

### 1a. The keyboard's own ceiling: the scan matrix, ghosting, and blocking

To keep wiring cheap, keyboard keys are not each given their own wire to the controller; they're
wired into a grid of rows and columns (the **keyboard matrix**), and the controller scans it by
driving one column at a time and reading which rows go active
([Deskthority wiki -- Keyboard matrix](https://deskthority.net/wiki/Keyboard_matrix)).

That sharing is the whole problem. If three keys that sit at three corners of a rectangle in the
matrix are held at once, current can flow backward through the fourth, unpressed corner and make
it look pressed too -- a phantom **ghost key**
([Deskthority wiki -- Rollover, blocking and ghosting](https://deskthority.net/wiki/Rollover,_blocking_and_ghosting)).
Cheap keyboards without protection either:

- **let it ghost** -- report a key nobody pressed, or
- **block** -- the controller is programmed to recognize a combination that *could* ghost and
  simply refuses to report the newer key at all, rather than risk a false key. Deskthority notes
  the safe cutoff most such designs settle on is **two** keys per matrix rectangle -- "any third
  key pressed after that is simply not registered" -- which is called **2KRO**.

Full **N-key rollover (NKRO)** fixes this at the hardware level: a diode behind every single
switch stops current from ever flowing backward, so any number of keys can be held with no
ghosting and nothing to block. This is a **per-key wiring decision made once, at manufacture** --
no amount of software downstream of the controller can add diodes that aren't there.

The critical, non-obvious part the owner's report already points at: **the ceiling is not a flat
number.** It's a property of *which keys share a row or column* in that specific keyboard's
matrix. Two keys that never share a rectangle can be held with a hundred other keys and never
collide; two keys that do share one may collide with nothing else held at all. That is exactly why
"about five notes" doesn't mean "five keys work, then it stops" -- it means "the chord shapes this
person happens to play collide about that often," which is a property of *this specific matrix*,
not of keyboards in general. Measuring it (below) has to be about combinations, not a count.

### 1b. The protocol's ceiling: what the USB HID boot report can carry, and what it can't

None of the above is specific to USB. USB adds a second, independent ceiling on top of it, and
it is a wire-format limit, not a matrix limit.

A USB keyboard's **boot protocol** report -- the format the HID 1.11 spec defines so a BIOS or
bootloader that can't parse an arbitrary Report Descriptor can still read a keyboard -- is a fixed
8-byte packet: **1 byte of modifier-key bits (Ctrl/Shift/Alt/GUI × left/right), 1 reserved byte,
and 6 bytes of keycodes**
([USB HID 1.11, Appendix B](https://www.usb.org/sites/default/files/documents/hid1_11.pdf);
explained clearly in [devever.net -- Myths about USB NKRO](https://www.devever.net/~hl/usbnkro)).
That's the origin of "6-key rollover" (6KRO) as a *protocol* term: eight modifiers plus **six**
non-modifier keycodes, full stop, because there are only six byte slots for them.

The boot protocol exists purely for pre-OS compatibility -- "BIOS writers might be neither able
nor inclined to incorporate a full implementation of HID which can parse arbitrary Report
Descriptors" is the reasoning devever.net gives, and it's borne out by the USB HID spec's own
framing of Boot Interface Descriptors as a fallback. Once a real OS driver loads, it doesn't have
to stay in boot mode: devever.net's point, worth taking at face value, is "a keyboard can still
implement the boot protocol *and also* provide NKRO... [via] its own preferred reporting formats."
The 6-key shape only constrains a device if the device's *own* Report Descriptor was written to
declare exactly 6 keycode slots -- which is a firmware/manufacturer choice copying the boot shape
for simplicity, not something USB forces on every keyboard. QMK (the open keyboard-firmware
project) documents this concretely: a "Standard" report is boot-protocol-identical (6 keys + 8
modifiers); an "Extended" report is N+2 bytes and boot-protocol-*compatible* but not limited to 6;
a "Bitmap" report can pack up to 128 or 256 keys, one bit each, N-key rollover
([QMK -- `docs/usb_nkro.txt`](https://github.com/qmk/qmk_firmware/blob/master/docs/usb_nkro.txt)).
None of that is exotic -- it's a standard, documented firmware feature that any USB keyboard's
manufacturer could ship, and it is orthogonal to whether the underlying matrix even has the diodes
to make the extra rollover meaningful (§1a and §1b are both required before more than ~6 keys can
ever reach the OS from a specific board).

PS/2 (the built-in `AT Translated Set 2 keyboard` on this machine) has no equivalent fixed-size
report at all -- it's a bidirectional serial link that sends one scancode event (a "make" or
"break" code) per key transition, so there is no protocol-level slot count to exhaust. This is the
real, historical reason PS/2 keyboards had a reputation for NKRO where cheap USB ones didn't: **the
protocol never capped them in the first place**. Whatever ceiling a PS/2 board hits is purely
§1a's matrix/ghosting/blocking ceiling, nothing else.

### 1c. The OS/driver's ceiling: essentially none, on Linux, but with a permissions cost

This is the ceiling most likely to be blamed and least likely to actually be the problem. Once a
report reaches the kernel, Linux's generic HID driver (`hid-generic`, run by the USB HID transport
driver `usbhid`) parses whatever Report Descriptor the *device* advertised and decodes exactly
that -- it is the older, legacy-specific `usbkbd`/`usbmouse` drivers that hardcode the boot format
without ever looking at the descriptor
([`drivers/hid/usbhid/hid-core.c`](https://github.com/torvalds/linux/blob/master/drivers/hid/usbhid/hid-core.c);
[Linux kernel docs -- HID I/O Transport Drivers](https://docs.kernel.org/hid/hid-transport.html)),
and `hid-generic` is what's actually bound on this machine (confirmed directly, §2). Above that,
the **evdev** abstraction that both PS/2 and USB keyboards end up funneled through is just a
stream of individual key-down/key-up events with no fixed-size array anywhere in it -- there is no
extra "OS can only track N keys" limit to find. So on Linux, the OS/driver layer is not a fourth
ceiling in practice; it faithfully relays whatever §1a and §1b already allowed.

Where the OS *does* impose a real cost is **permissions**, not a rollover number: `/dev/input/eventN`
is owned `root:input`, mode `660` (confirmed on this machine, §2), so reading raw key events at
all (rather than through a desktop toolkit) needs either root or membership in the `input` group.
That's a portability concern of its own, discussed in §3 and §4.

### Summary: three ceilings, three remedies

| Ceiling | What sets it | Who can fix it | Fix |
|---|---|---|---|
| Keyboard/matrix | Per-key diode wiring (or lack of it) at manufacture | The keyboard's manufacturer, at design time | Buy a keyboard advertised as full NKRO |
| Protocol (USB only) | Whether the device's *own* Report Descriptor uses a 6-slot boot-shaped array or a bitmap/extended report | The keyboard's firmware (manufacturer, or reflashable firmware like QMK) | A firmware update or a different board; PS/2 has no such ceiling at all |
| OS/driver (Linux) | Not really a ceiling -- evdev has no cap | N/A in practice | N/A; the real Linux-side cost is *permission* to read the device at the level needed |

Nothing here can be patched from inside note-color. All three fixes live outside the app, which is
exactly why §4 ends where it does.

---

## 2. This machine, measured

### What's actually attached

```
AT Translated Set 2 keyboard      -- built-in ThinkPad T480 keyboard, i8042/atkbd (PS/2-style)
Telink Wireless Receiver           -- USB dongle (VID 248a "Maxxter", PID 8514), 3 HID interfaces:
  if00 (Mouse)                     -- mouse movement/buttons
  if00 (System Control)            -- power/sleep keys, separate logical device on the same interface
  if01 (Keyboard, Boot Interface)  -- the actual alphanumeric keyboard, /dev/input/event7,
                                       by-id: usb-Telink_Wireless_Receiver-if01-event-kbd
```
(`cat /proc/bus/input/devices`, `lsusb -v -d 248a:8514`, run directly on the machine.)

### The Telink keyboard's protocol ceiling is measurable without a single key press

I pulled the USB HID **Report Descriptor** directly from the device
(`/sys/class/hidraw/hidraw1/device/report_descriptor`, the sysfs node backing the keyboard
interface) rather than inferring it, since the descriptor is the primary source for what this
exact unit can ever report:

```
05 01 09 06 A1 01 05 07 19 E0 29 E7 15 00 25 01 95 08 75 01 81 02 81 03
95 05 05 08 19 01 29 05 91 02 95 01 75 03 91 01 95 06 75 08 15 00 26 A4
00 05 07 19 00 2A A4 00 81 00 C0
```

Decoded, this is:

- `Usage Page (Generic Desktop) / Usage (Keyboard)` -- an Application Collection
- `Usage Page (Keyboard/Keypad), Usage Min E0, Usage Max E7` (the eight modifier usages),
  `Report Count 8, Report Size 1` -- the **1-byte modifier bitfield**
- `Report Count 1, Report Size 8, Const` -- the **1 reserved byte**
- (an Output report for the 5 keyboard LEDs, irrelevant here)
- `Report Count 6, Report Size 8, Logical Max 0xA4, Usage Page (Keyboard/Keypad),
  Usage Min 0, Usage Max 0xA4, Input (Data, Array, Abs)` -- **6 bytes, each one keycode slot**

That is the USB HID **boot keyboard report, byte for byte** -- 1 modifier byte + 1 reserved byte +
a 6-slot keycode array -- and it is what *this device itself* declares as its only input report,
not a fallback Linux is forcing it into (§1c: `hid-generic` parses this descriptor as given; it is
not running the device in a degraded boot-only mode). **This specific wireless keyboard cannot
ever report more than 6 simultaneous non-modifier keys, on any OS, under any configuration** --
that ceiling is baked into the firmware's own report shape. It is a §1b (protocol/firmware)
ceiling, fully independent of whatever the physical matrix underneath it can or can't do -- the
matrix ceiling (§1a) can only be *lower* than 6, never higher, given this report shape.

This single fact is already most of the explanation for "about five": with zero note-key
modifiers active, the practical ceiling for a chord played on this board is at most 6, and if even
one pair of keys in the chord's matrix also collides (§1a), it drops below that -- landing right
around the "about five, and which notes survive varies" the owner described. I could not similarly
inspect the built-in keyboard's ceiling this way, because it isn't a USB HID device at all -- see
below.

### The built-in keyboard has no equivalent descriptor to read

`AT Translated Set 2 keyboard` is a PS/2-protocol device behind the `i8042` controller (confirmed:
`Handlers=sysrq kbd leds event3`, `Phys=isa0060/serio0/input0`, no `hidraw` node exists for it at
all). Per §1b, PS/2 has no report descriptor and no fixed slot count to inspect -- its ceiling is
purely whatever the ThinkPad's embedded keyboard controller and physical matrix support, which
**can only be found by holding real keys down**, not by reading a byte layout.

### Which keyboard does the owner actually play on?

I could not establish this with confidence. What I could check: the laptop's lid is open
(`/proc/acpi/button/lid/*/state` reads `open`), so the built-in keyboard is physically available,
*and* the Telink wireless keyboard+mouse combo is paired and present at the same time. A wireless
keyboard+mouse combo alongside an open laptop is the pattern of someone sitting at a desk who
prefers an external mouse over the trackpad -- circumstantially suggesting the Telink board is the
daily driver -- but that is an inference, not a measurement, and it could just as easily be a
spare/backup keyboard. **This needs to be confirmed by asking the owner directly**, or, better,
by running the probe below on both and comparing against what "felt like about five" in #221.

### I could not physically measure the collision set myself

Measuring the *matrix* ceiling (as opposed to the protocol ceiling above, which needed no key
presses at all) requires a human's fingers actually holding down specific chord shapes on a real
keyboard, for two independent reasons neither of which I can get around:

1. **Permission.** `/dev/input/event6` and `/dev/input/event7` are `root:input`, and this
   session's user (`pelle`) is only in `wheel`, not `input` -- reading the raw event stream
   requires `sudo` or joining the `input` group first (confirmed via `ls -la`; see §1c).
2. **Physical presence.** Even with permission, an agent has no way to press real keys on real
   hardware in real chord shapes. This is not a permissions problem to work around; it's a
   fundamental limit of what I can do here.

So per the issue's own fallback plan, I wrote the measurement script instead and did not run it.
That is, per the issue, "a perfectly good outcome, better than a guess" -- and the Telink
descriptor finding above means this isn't a total loss even for that board: we already know its
hard ceiling is 6, we just don't yet know which specific chord shapes fall under it.

### The script: `rollover_probe.py`

Needs `pip install evdev`. Run with `sudo`, or after `sudo usermod -aG input $USER` (log out and
back in first).

```python
#!/usr/bin/env python3
"""rollover_probe.py -- measure a keyboard's real simultaneous-key ceiling.

Usage:
    python3 rollover_probe.py <device-path-or-name-substring>

Examples:
    sudo python3 rollover_probe.py /dev/input/by-id/usb-Telink_Wireless_Receiver-if01-event-kbd
    sudo python3 rollover_probe.py "AT Translated Set 2"

Needs the 'evdev' package (pip install evdev) and read access to the
device node. /dev/input/event* is root:input, mode 660 on most distros,
so either run this with sudo, or add yourself to the input group once
(`sudo usermod -aG input $USER`, then log out and back in) and drop sudo.

What it does
------------
Opens one evdev keyboard device and tracks the live set of keys currently
held down, built only from the EV_KEY down (value 1) / up (value 0) events
the kernel driver actually reports -- autorepeat (value 2) is ignored, and
nothing here injects or assumes anything the hardware didn't report. This
is exactly the same event stream note-color's Qt widgets sit on top of, so
whatever ceiling this script sees is the same ceiling the app is fighting.

Prints a line every time the held-key set changes, then on Ctrl-C prints
the largest simultaneous count seen and the key combination at that peak.

How to use it to find this keyboard's ceiling
----------------------------------------------
1. Run it pointed at ONE keyboard device at a time (run it twice, once
   per device, if you're not sure which one you play on).
2. Play the two-octave layout's actual chords (see
   src/notecolor/tui/synth_layout.py, two_octave_layout):
       upper black:  2 3   5 6 7
       upper white:  q w e r t y u
       lower black:   s d   g h j
       lower white:  z x c v b n m
   Start with two-note intervals, then triads, then sevenths. Try shapes
   that use only the white row, then shapes that span black+white under
   one hand, then full four-row spans across both hands, since that's
   what a real chord uses.
3. Watch the "[N held]" line. When N stops climbing even though you're
   still adding fingers, that's the ceiling for that hand position. Try
   several chord shapes -- the point of this script is that the ceiling
   is not one flat number, it depends on which keys collide, which is
   exactly why the number you can play varies press to press.
4. Ctrl-C to stop. Report back (or paste into the research doc / issue):
   the max count, and which specific keys were in play when a chord felt
   short a note.
"""
import sys
import time
from collections import OrderedDict

try:
    import evdev
    from evdev import ecodes
except ImportError:
    sys.exit("Needs the 'evdev' package: pip install evdev")


def find_device(hint):
    """hint may be a /dev/input/event* (or by-id) path, or a case-insensitive
    substring of the device's advertised name (e.g. 'Telink' or 'AT Translated')."""
    if hint.startswith("/dev/"):
        return evdev.InputDevice(hint)
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if hint.lower() in dev.name.lower():
            return dev
    sys.exit(
        f"No input device matched {hint!r}. List candidates with:\n"
        "  cat /proc/bus/input/devices\n"
        "or: python3 -c \"import evdev; [print(evdev.InputDevice(p).path, evdev.InputDevice(p).name) for p in evdev.list_devices()]\""
    )


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} <device-path-or-name-substring>")

    dev = find_device(sys.argv[1])
    print(f"Reading {dev.path} ({dev.name!r}) -- Ctrl-C to stop and see the summary\n")

    held = OrderedDict()   # keycode -> name, in press order
    max_seen = 0
    max_combo = ()

    try:
        for event in dev.read_loop():
            if event.type != ecodes.EV_KEY:
                continue
            if event.value == 1:            # down
                held[event.code] = ecodes.KEY.get(event.code, f"CODE_{event.code}")
            elif event.value == 0:          # up
                held.pop(event.code, None)
            else:
                continue                     # 2 == autorepeat: not a new press

            n = len(held)
            print(f"[{n:2d} held] {', '.join(held.values())}")
            if n > max_seen:
                max_seen = n
                max_combo = tuple(held.values())
    except KeyboardInterrupt:
        pass
    finally:
        print("\n--- summary ---")
        print(f"Max simultaneous keys observed: {max_seen}")
        print(f"Combination at max: {', '.join(max_combo) if max_combo else '(none)'}")
        print(
            "If this is lower than the number of fingers you actually had "
            "down, that's the drop happening -- the keyboard/OS never told "
            "this script (or note-color) that key was pressed at all."
        )


if __name__ == "__main__":
    main()
```

A zero-install cross-check exists too: browser-based rollover testers (e.g.
[keyboardtest.io](https://keyboardtest.io/keyboard-rollover/)) read the same OS-decoded key events
at the DOM level and show a live held-key count plus a flashing "signal received" indicator, which
the site itself explains: "if a third key doesn't register **and** the signal light doesn't flash,
the keyboard hardware isn't sending that key's signal (blocked by conflict)." It won't know
anything about note-color's own layout, but it's a fast independent second opinion on the same
number, and needs no Python setup at all.

---

## 3. What other software does

### DAWs and soft synths: nobody works around the rollover limit; some document it, most don't

- **Ableton Live -- Computer MIDI Keyboard.** Ableton's own manual is explicit that this is a
  distinct, separate feature from remote-control key mappings
  ([Ableton -- MIDI and Key Remote Control](https://www.ableton.com/en/manual/midi-and-key-remote-control/)),
  but I could not fetch its dedicated "Playing MIDI With the Computer Keyboard" manual page (403
  on repeated attempts) or Ableton's own help-center article on the feature retriggering/dropping
  notes, so what follows is from search-result summaries of those pages, not text I read directly
  -- flagging it as **not independently verified**: community reports (forum threads, secondary
  write-ups) describe the feature as effectively near-monophonic in practice, and attribute this
  explicitly to keyboard matrix wiring rather than to Ableton's own code. Treat this one as
  probable, not confirmed.
- **Logic Pro -- Musical Typing** (verified via search-result text of Apple's own support page,
  though I could not fetch the page itself to quote it directly): dedicates whole extra keys to
  the expression #173 is about, rather than doing anything about polyphony -- `C`/`V` for
  velocity down/up, `3`-`8` for modulation wheel value (`3` off), `Tab` for sustain, `1`/`2` for
  pitch bend, `Z`/`X` for octave shift. This is the most complete answer any of the surveyed tools
  give to the "missing expression" half of #173, and it's a pure keystroke-simulation trick: more
  QWERTY keys standing in for MIDI CCs, not a rollover fix, and not requiring a second HID path.
- **FL Studio -- Typing Keyboard To Piano.** Same pattern (secondary sources: how-to sites, an
  Image-Line forum thread): white keys start at a letter row, black keys on the number row above.
  Multiple independent how-to sources note as a known caveat that "pressing certain multiple key
  combinations cut out due to how typing keyboards are linked" -- i.e., FL Studio's own community
  documents the exact same matrix-collision behaviour and treats it as an accepted fact of QWERTY
  input, not something the app tries to fix.
- **VCV Rack -- Computer keyboard MIDI driver** (primary source: the VCV Rack manual). Spans "the
  QWERTY and ZXCVB rows," roughly 2½ octaves across two note rows, shiftable with `` ` `` and `1`.
  Separately, its MIDI-CV module documents three note-to-channel allocation policies for
  polyphony -- **Rotate** (next available channel, wrapping), **Reuse** (same note reuses its
  previous channel), **Reset** (lowest available channel, with everything above it shifting down
  on release) -- but these govern how *already-received* note-on messages are assigned to
  polyphonic channels; none of them can recover a keydown the keyboard hardware never sent in the
  first place. This is the same distinction as #221 vs #226 for note-color: voice allocation
  policy and input rollover are separate problems, and no allocation policy fixes the other one.
- **Browser-based virtual pianos.** Consistently keydown/keyup-driven (so bound by the same OS-
  decoded event stream as everything above); sustain is commonly mapped to Space, sometimes
  CapsLock. I found no browser piano documenting a mod-wheel equivalent at all, and no browser tool
  that does anything about rollover beyond accepting whatever the browser's keydown stream reports.

### Does anyone read raw HID to get past the boot-protocol limit?

One real example, not a hypothetical: **[HIDI](https://github.com/gethiox/HIDI)** ("flexible HID
to MIDI translation layer"), a Go project built specifically to turn ordinary keyboards (and
gamepads) into MIDI controllers. It's the closest thing in this survey to "what if note-color read
lower-level input to fix this," and it's informative precisely because of what it *doesn't* solve:

- It reads via Linux **evdev**, not raw `hidraw` -- i.e. the same abstraction layer note-color's
  Qt widgets sit on top of, one level higher than the USB wire format, not lower.
- It's Linux-only, but does ship ARM builds explicitly for Raspberry Pi-class hardware -- the same
  end of note-color's own portability range (CLAUDE.md).
- It needs the same `input` group membership or `sudo` this project's own probe script needs
  (§2), plus an optional `-grab` flag to take exclusive control of the device so key presses stop
  reaching the rest of the OS while it's running.
- Its own documentation admits that automatically negotiating a keyboard's proprietary NKRO mode
  from the OS side is unsolved even in this purpose-built project -- it can use NKRO if a keyboard
  already exposes it (or can be switched into it by a hardware key combo), but it doesn't claim to
  unlock 6KRO hardware.

That last point is the whole finding, stated plainly: **even a project built for exactly this
purpose does not get past a keyboard whose matrix or firmware caps it at 6KRO.** Reading events
one layer lower than Qt gets you the same events Qt already has, plus a permissions requirement,
plus (per §1a/§1b) nothing that wasn't already going to be capped before it ever reached user
space. The fix that actually works is upstream of all of this: a keyboard whose firmware +
matrix support NKRO, or real MIDI hardware, which sends independent note-on/off messages per key
with no shared array to exhaust at all.

### What this would cost note-color specifically

Reading raw `hidraw` (rather than evdev) would add nothing the descriptor dump in §2 didn't
already get without any key presses or elevated access at all -- and going further, to a live
raw-HID event loop, would mean:

- A second, parallel low-level input path alongside Qt's own, maintained solely to serve one
  widget (`SynthKeyboardBand`).
- `root:input`-gated device access on **every** target the app runs on, from Raspberry Pi class up
  to desktop (the portability range CLAUDE.md commits to) -- not a one-time setup cost, a
  permissions story that has to work identically on every deployment.
- A platform-specific concept to begin with: `hidraw` is Linux-only. Windows and macOS have their
  own, entirely different raw-HID APIs, so this path does not generalize even within "desktop."
- All that cost, for a capability that (per HIDI's own admission above) still cannot exceed
  whatever the specific keyboard's matrix and firmware already cap it at.

---

## 4. What it means for note-color

**What is actually available to us:** nothing, in software, that increases how many keys a given
keyboard can report at once. Section 1 established that as three ceilings owned by the keyboard's
manufacturer (matrix diodes), its firmware (report shape), and -- on Linux -- essentially nobody
(evdev has no cap of its own). Section 2 measured one of those ceilings directly on this machine:
the Telink wireless keyboard's own USB HID Report Descriptor is the 6-slot boot-keyboard shape,
so **six simultaneous non-modifier keys is this specific board's hard ceiling, on any OS, under any
software** -- and the "about five, and which notes survive varies" the owner reported is fully
consistent with that ceiling plus ordinary matrix collisions eating one more slot depending on hand
shape. Section 3 found that no DAW, soft synth, or browser piano surveyed does anything to lift
this; the ones that address it at all either document it as an accepted limitation (FL Studio's
community docs) or point at real MIDI hardware.

**The honest answer is exactly that:** get a keyboard that's actually built for it (advertised
NKRO, which still means checking the matrix and firmware, not just a marketing label), or use MIDI
hardware, which is [#173](https://github.com/pellepang/note-color/issues/173)'s territory -- a
MIDI note-on message carries its own note number with no shared 6-slot array to exhaust, so the
whole rollover problem this document is about simply does not exist on that path. Logic Pro's
Musical Typing (§3) is the right model for what QWERTY input *can* still contribute meaningfully:
richer expression via more assigned keys (velocity, mod wheel, sustain, pitch bend), which is
orthogonal to polyphony and doesn't require fighting the matrix at all.

**How the app should tell the user.** The worst outcome, named directly in the issue, is silently
dropping notes -- which is what happens today. Two things are worth separating:

- **A key that's actually swallowed by the keyboard hardware is invisible to note-color and to
  every other layer of software above it.** `SynthKeyboardBand.keyPressEvent` (and Qt's own event
  loop underneath it) only ever sees a keydown that made it all the way through the matrix, the
  USB report, and the OS; a key eaten by ghosting/blocking (§1a) or absent because a 6-slot report
  was already full (§1b) never generates an event to catch, by definition. No code inside the app
  can detect that specific drop after the fact — it looks identical to "the user didn't press
  that key." This isn't a note-color-specific gap in instrumentation; §3's raw-HID survey found
  the same blind spot one layer lower, in a project built to solve exactly this.
- **What *is* detectable, and what already exists to say it with:** `SynthKeyboardBand` already
  tracks its own live `_held` dict of every key Qt told it is down, and `SynthView` already has a
  status bar (`_build_status_bar()`) with a precedent for exactly this kind of live, low-key
  explanatory text -- `_status_patch` already exists there to explain "what the cables are doing,
  and where a refusal says its piece" (decision 57 §5). A `_status_rollover`-style label in that
  same bar, using the same convention, is the natural place to say something the moment a chord
  this large is attempted at all -- e.g. surfacing a note the first time (or every time, dismissed
  once per session) the number of notes the user is *trying* to hold crosses a threshold like 5-6,
  regardless of whether every one of them actually sounded: "large chords may drop notes on this
  keyboard -- see #173 for MIDI input." That sidesteps the undetectable-drop problem entirely by
  warning on attempted chord size rather than on the drop itself, which is the one thing the app
  genuinely can observe.
- Whatever UI note-color settles on, it should stop implying a polyphony number the input path
  can't deliver: the engine's 16-voice budget is real, but showing it unconditionally while the
  active input is a QWERTY keyboard overstates what a chord played on this specific hardware can
  actually reach -- a number that, per this whole document, depends on the keyboard, not the
  engine.

---

## What I could not verify / could not do

- **The Synth View's actual matrix ceiling was not measured.** I have shell access to this
  machine but am not in the `input` group and cannot physically hold down keys -- both the
  permission gap and the "an agent has no fingers" gap are explained in §2. `rollover_probe.py`
  above is written and ready for the owner to run on both keyboards; I did not run it.
- **Which keyboard the owner actually plays the Synth View on** is inferred (lid open + a paired
  wireless combo, suggesting a desk setup with an external mouse), not confirmed. Worth asking
  directly, or letting the probe results settle it against what "about five" felt like.
- **Ableton Live's own documentation of the Computer MIDI Keyboard's limitations** could not be
  fetched directly (its help-center article and the manual's dedicated page both returned HTTP 403
  to automated fetches on every attempt); the near-monophonic characterization above comes from
  search-result summaries of community/forum discussion of that page, not text I read myself.
  Treat it as probable, not confirmed.
- **No primary source found comparing PS/2 vs USB rollover** in one place; the claim in §1b is
  assembled from the USB HID spec (what USB does cap) plus general descriptions of PS/2 as a
  serial, one-scancode-at-a-time link (what it structurally can't cap), rather than a single
  citable document making the comparison directly.
- **The built-in ThinkPad T480 keyboard's actual matrix ceiling** has no equivalent to the Telink's
  report-descriptor shortcut (§2) -- PS/2 devices don't expose a descriptor to read -- so nothing
  about it could be established without physically pressing keys, which is exactly what the probe
  script is for.

## Sources

- USB Implementers Forum -- [Device Class Definition for Human Interface Devices (HID), v1.11](https://www.usb.org/sites/default/files/documents/hid1_11.pdf), Appendix B (Boot Interface Descriptors)
- [devever.net -- Myths about USB NKRO and how USB HID works](https://www.devever.net/~hl/usbnkro)
- Deskthority wiki -- [Keyboard matrix](https://deskthority.net/wiki/Keyboard_matrix), [Rollover, blocking and ghosting](https://deskthority.net/wiki/Rollover,_blocking_and_ghosting)
- QMK Firmware -- [`docs/usb_nkro.txt`](https://github.com/qmk/qmk_firmware/blob/master/docs/usb_nkro.txt)
- Linux kernel -- [`drivers/hid/usbhid/hid-core.c`](https://github.com/torvalds/linux/blob/master/drivers/hid/usbhid/hid-core.c), [HID I/O Transport Drivers docs](https://docs.kernel.org/hid/hid-transport.html)
- Ableton -- [MIDI and Key Remote Control](https://www.ableton.com/en/manual/midi-and-key-remote-control/) (fetchable page); the feature's own dedicated manual page and help-center article were not fetchable (403) -- see "What I could not verify"
- Apple -- Logic Pro Musical Typing keybindings, via search-result summary of [Play software instruments in Logic Pro](https://support.apple.com/guide/logicpro/play-software-instruments-lgcpb19cbd34/mac) (page itself not independently fetched)
- Image-Line community / how-to sources on FL Studio's Typing Keyboard To Piano (secondary; no single Image-Line manual page fetched)
- VCV Rack Manual -- [Key Commands](https://vcvrack.com/manual/KeyCommands), [Getting Started](https://vcvrack.com/manual/GettingStarted)
- [gethiox/HIDI](https://github.com/gethiox/HIDI) -- HID-to-MIDI translation layer, evdev-based, ARM/Raspberry Pi builds
- [keyboardtest.io -- Keyboard Rollover Tester](https://keyboardtest.io/keyboard-rollover/)
- Local, primary measurements on this machine: `/proc/bus/input/devices`, `lsusb -v -d 248a:8514`,
  `/sys/class/hidraw/hidraw{0,1}/device/report_descriptor`, `/sys/class/dmi/id/*`,
  `/proc/acpi/button/lid/*/state`, `ls -la /dev/input/event{3,6,7}`
