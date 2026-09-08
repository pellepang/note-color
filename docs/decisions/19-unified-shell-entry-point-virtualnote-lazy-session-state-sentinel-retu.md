# Unified shell entry point: `virtualnote`, lazy session state, sentinel returns (issue #40)

Map #37's grilling (#39) had already settled the shape (one new command,
in-process not subprocess-per-tool, `|`/`H` as the global keybinds) — #40's
actual work was making `main.py`'s existing per-invocation architecture
support running any tool, any number of times, in one process, without
disturbing anything the three terminal views/GUI already did.

**One `SessionState`, not `run_session()`'s originally-sketched loose
params.** The ticket's own sketch suggested a signature like
`run_session(view, ..., sensitivity, source_state, capture_holder)` — five-
plus loose parameters threaded through. Bundling `capture`/`result_queue`/
`stop_event`/`analysis_thread`/`sensitivity`/`source_state`/`color_scheme`
into one `main.SessionState` object instead keeps `run_session()`'s own
signature to `(view, scroll_mode, dump_file, fullscreen, debug, session)`
and gives the lazy-start/idempotent-restart logic (`ensure_started()`) a
natural home next to the state it manages, rather than as free-floating
module-level logic that would have to reach into five separate variables.
Nothing about this changes the *behavior* the ticket asked for, only how
the plumbing is packaged.

**Lazy creation, not eager-at-process-start.** `virtualnote`'s bare menu
must not open the mic just from being displayed — a real, user-visible
side effect (OS "listening" indicators, an OS mic-permission prompt on
macOS/Windows) that has no business firing before a tool is actually
picked. `SessionState.ensure_started()` is a no-op once already started,
so both call sites — `main()`'s single eager call (preserving today's
exact "capture opens immediately" behavior for anyone still running
`main.py` directly) and `shell.py`'s menu loop calling it fresh before
every tool entry — share one code path with no special-casing for which
caller it is.

**Sentinel return value, not an exception or a shared flag.** The ticket
considered (and rejected) two alternatives for signaling "the user pressed
`|`, go back to the menu": (1) a custom exception type unwound by the
caller, which would have meant every terminal `run_*` function's existing
`try/except KeyboardInterrupt/finally` shape needed a second parallel
exception-handling path; (2) a shared mutable flag object threaded through
like `Sensitivity`/`SourceState`, which adds shared state for something
that's really just "what did this call return," the textbook case for a
plain return value. `"quit"`/`"menu"` as plain strings (not an enum — this
app has no other enums, and two well-named string constants read fine at
every call site) slot into the exact same `finally` blocks that already
ran `keys.restore()`/`display.quit()`/`dump_ansi()` before this ticket,
with `return "menu"` fired from inside the existing `try` and Python's
normal "execute `finally` before actually returning" semantics doing the
rest — no restructuring of any run_* function's control flow beyond
adding the one new keycheck and swapping `pass`/implicit-`None` for an
explicit `return "quit"` in the `except KeyboardInterrupt` clause.

**GUI's back-to-menu key is `K_BACKSLASH`, wired directly in `run_gui`,
not shared with the terminal views' `_handle_back_to_menu_key`.** Pygame
reports the physical backslash key as `K_BACKSLASH` regardless of the
shift modifier — there's no separate keycode for the shifted `|` glyph the
way a raw terminal byte stream just hands over the literal character. Since
`_handle_back_to_menu_key(key)` compares against the literal string `"|"`,
it can't be reused as-is for a pygame `KEYDOWN` event; `run_gui` checks
`event.key == pygame.K_BACKSLASH` inline instead, same as it already does
for `K_ESCAPE`/`K_f`/`K_d`. `H` similarly gets its own inline
`pygame.K_h` check in `run_gui` rather than going through
`_handle_help_legend_key` (which expects a single-character string, not a
pygame keycode) — both are still the same conceptual toggle, just two
small platform-appropriate call sites instead of one shared function
that would need an awkward key-representation-normalizing shim in the
middle for no real benefit (each call site is one line).

**H legend line reserves a second trailing terminal row only when
populated.** Every terminal view's `render()`/`render_bands()`/
`render_chord()` grew an optional `legend=""` (`help_legend=""` on tab's
`TabDisplay.render()`, to avoid colliding with its existing, unrelated
`legend_on` staff-legend-column parameter) trailing-row parameter, sized
into each view's existing "reserve N rows for text, fill the rest with the
view's actual content" row-budget math. Passing `""` (H off) reproduces
the exact single-status-row layout every view always had; a non-empty
string reserves one more row and draws it right below the status line.
Kept as a plain string built by `main._legend_line()`, not a structured
type or its own rendering primitive — same "informational text line, not
a UI framework" scope call #40's ticket asked for; #42 owns the shell's
actual visual design and can restyle this without touching the row-budget
mechanics underneath it.

**Tab's on-quit `dump_ansi()` still fires on a `"menu"` return, not just
`"quit"`.** `run_terminal_tab`'s `finally` block was already
`keys.restore()` wrapping a nested `try: dump_ansi() finally: display.quit()`
before this ticket; `return "menu"` executes through that exact same chain
(Python runs `finally` blocks on every `return`, not only on an exception),
so leaving `tab` via `|` writes the session's note history same as leaving
it via Ctrl+C always did — there was no reason to special-case "the history
dump only matters if you're quitting the whole process," a session that
moves on to another tool via the menu still benefits from that file.
