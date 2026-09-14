# 61. The Mix node, the poly boundary, and where summing is allowed (ticket #204, map #179)

`audio/graph/poly.py`. Third ticket of decision 56's engine stream, plus the
owner's answer to the question #203 raised.

## 1. Where summing is allowed (the owner's call)

#204 as written said Mix is "the only place summing happens". Building #203
turned up a case that does not cover: **Osc 1 and Osc 2 into one Filter,
inside a single voice**, which is the most ordinary patch in subtractive
synthesis and which Mix cannot perform, because Mix *is* the boundary --
anything through it comes out once-only.

Offered three ways (inputs sum silently / one cable per input plus a
poly-side Mixer module / inputs sum and the canvas marks the jack), the
owner took the third.

So: **several cables may meet at one input and are summed there, and the
canvas marks any jack carrying more than one cable.** `ModuleGraph.
summed_inputs()` publishes exactly where that happens, so the canvas draws
it rather than inferring it by counting cables itself and the two can never
disagree.

This does not weaken decision 56 §3, and it is worth being precise about
why. §3's objection was never to addition; it was to a *voice count*
collapsing somewhere the screen does not admit to. Sixteen becoming one still
happens only at Mix. Two signals inside one voice meeting at a marked jack is
a different operation, and now a visible one.

## 2. The split is decided by the cables, not by the module

A node that can reach Mix is on the per-note side; a node Mix can reach is on
the once-only side. A module declaring `POLY_PER_NOTE` or `POLY_ONCE`
constrains where it may be cabled, but most modules declare `POLY_EITHER` and
for those **the patch decides** -- which is the point of having a patch.

An unpatched module falls back to its own declaration, and to the once-only
side when it has none: it has no voice to belong to.

This split is why the refusal rule needed two halves. `graph.judge()` knows
what a module *declares* and catches the declared crossing on its own;
`PolyGraph.judge()` adds the crossings that exist only because of the patch,
which is the call the canvas should make. `PolyGraph.activate()` refuses to
build a patch that already contains one, rather than silently dropping the
cable -- which is what an earlier draft did, and it took a test to notice.

## 3. `is_boundary` is a flag, not a fourth poly mode

Mix belongs to neither side: its inputs are per-note (that is what it is
summing) and its output is once-only. A fourth value of `poly` could not
express that, because every rule that asks about poly is really asking about
*which end of the cable*, and one field cannot answer two questions.

## 4. Voice tear-down is a module's decision

`note_off()` clears the gate and does nothing else. A voice is reclaimed when
a module sets `note.finished`, which is an amp envelope's job (#205).

Until one exists, a released note **drones** -- which is exactly what a
modular with no envelope patched does, and is deliberately not papered over.
The alternative is the voice manager deciding when a note has stopped
sounding, which is how a synth ends up clicking on release: something cuts
the signal at a block boundary regardless of where the waveform was.

Sixteen complete copies of the per-note subgraph are built in `activate()`
through `Module.new_instance()`, all off the audio thread, because a note-on
that allocates is a note-on that can miss its deadline. Slots are reused
forever: retiring a voice resets its modules, it never frees them.

Stealing is the oldest released voice, else the oldest voice -- the same
policy `sound_engine.select_steal_index()` reached (decision 38), written
separately because this one steals *subgraphs* and sharing the function would
mean sharing a data model neither side wants.

## 5. Note-off touches nothing right of Mix

Which is what keeps a delay's tail ringing after the key is up, and is the
practical argument for drawing the boundary at all: the two sides have
different lifetimes and the canvas says which is which. Tested directly --
the voice is gone, Mix is silent, and the once-only delay is still decaying.
