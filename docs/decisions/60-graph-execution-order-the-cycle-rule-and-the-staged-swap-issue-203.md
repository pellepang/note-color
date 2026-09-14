# 60. Graph execution order, the cycle rule, and the staged swap (ticket #203, map #179)

The second ticket of decision 56's engine stream. `audio/graph/graph.py`:
the thing that holds modules and cables and decides what runs when.

## 1. Two objects, because there are two threads

`ModuleGraph` is the editable patch -- nodes, cables, `judge()`. It may
allocate, raise and take its time. `CompiledGraph` is what the audio thread
runs: a flat tuple of steps in execution order with every buffer already
bound, immutable by construction.

`compile()` turns the first into the second, and that is the whole of "graph
edits are staged and swapped". The host holds one attribute pointing at a
`CompiledGraph`; an edit builds a replacement off the audio thread and
rebinds that attribute, a single store the callback either sees or does not.
No lock, no queue, no torn state, because nothing is ever mutated in place.
There is deliberately **no incremental rebind path** -- rewiring recompiles
the whole patch, which at this size is microseconds, and an incremental
binder is precisely where torn state would come from.

## 2. Ordering: the cycle is ordered, not solved

Kahn's algorithm, ties broken by insertion order so two identical compiles
produce identical orders (a graph that reorders itself makes every ordering
bug unreproducible).

The one real idea: **a cable leaving a module with `block_delay >= 1` does
not constrain the order.** That module's output this block was computed from
input it read a block ago, so whatever it feeds may legitimately run before
it. Cut those edges and a legal feedback loop is an ordinary DAG.

This also collapses the cycle *rule* into the same fact. Decision 56 §4 says
a loop is legal only through a delay; asked as an ordering question, that is
just "does the proposed cable close a cycle among the constraining edges?"
-- a loop containing a delay has already vanished from that graph, so what
is left to refuse is exactly the illegal case. One search, not two. The
refusal names every module in the loop, because a refusal that only asserts
a loop exists sends the user hunting for it.

`compile()` still raises `CycleError` if an illegal cycle reaches it. That
is unreachable through `judge()` and exists because tests build forbidden
patches on purpose (`force_connect()`), and because a defence that depends
on a caller having asked politely is not a defence.

## 3. Summing where several cables meet one input

Several cables may land on one input port; the graph sums them, and
`compile()` allocates a buffer for it. One cable is bound **straight
through** to the producer's own output buffer -- no module writes to its
inputs, so a copy per cable per block would be pure cost -- and an
unconnected input is bound to a shared zero buffer, so no module ever tests
for `None` and no patch-dependent branch enters the callback.

**This contradicts #204's wording** ("Mix sums its per-note inputs. It is the
only place summing happens") and is flagged there rather than settled here.
The reason for going this way in the meantime: decision 56 §3's rule is
about the **poly boundary** -- sixteen voices must not silently become one
anywhere except where it is drawn -- and two sources meeting inside one
voice is a different operation. osc1 and osc2 into one filter is the most
ordinary patch there is, Mix cannot do it (anything through Mix comes out
once-only), and `gui/patch_graph.socket_counts()` already draws a module as
having as many input holes as it has cables plus a spare. If the owner wants
one cable per input instead, it is a rule added to `judge()`, not a rewrite.

## 4. The refusal contract

`Verdict(ok, code, reason)` -- both a code the UI can style and branch on,
and a sentence for the person holding the cable, because the code cannot be
shown to anyone and the sentence cannot be branched on. Codes: unknown
node, unknown port, direction, type, poly, cycle, duplicate, self.

`gui/patch_graph.PatchGraph.judge()` was written before this existed, shaped
to match, and is still the canvas's stand-in. It becomes a delegation once
canvas nodes are backed by real modules, which is #204/#211 -- not now,
because today's canvas nodes have no modules behind them at all.

## 5. Stability is the patch's business

A loop through a delay at unity gain runs away, and nothing refuses it. That
is correct and is what a real modular does; the graph's job is to make the
path legal and audible, not to protect the user from a patch they can hear
going wrong. The test pins it at a loop gain of 0.4 and separately checks
that silencing the oscillator leaves the echo ringing and decaying -- the
audible proof that sound is travelling back through the delay rather than
merely passing through it.
