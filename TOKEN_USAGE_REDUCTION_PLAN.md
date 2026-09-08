# Token Usage Reduction Plan — note-color

Read-only audit performed 2026-09-08. **Update: Change A and Change C
below have since been applied, with your approval — see "Results" at the
end of this document.**

## 1. Environment

- Working directory: `/home/pelle/note-color`
- Git repo: yes, remote `github.com/pellepang/note-color`, branch `main`,
  working tree clean.
- Not a monorepo. One Python package (`src/notecolor/`) plus tests,
  scripts, prototypes, and docs. Starting Claude Code from a subdirectory
  would **not** help — the cost identified below comes from a single file
  at the repo root that's loaded regardless of cwd, not from scanning the
  tree.

## 2. Instruction/config/memory inventory

| Item | Location | Size | Auto-loaded every session? |
|---|---|---|---|
| Project `CLAUDE.md` | `/home/pelle/note-color/CLAUDE.md` | **283,367 bytes / 1,970 lines / ~35,700 words** | **Yes — in full** |
| Global `CLAUDE.md` | `~/.claude/CLAUDE.md` | 228 bytes | Yes (tiny, just a graphify trigger note) |
| `docs/DECISIONS.md` | repo root | 302,561 bytes | **No** — only referenced by pointer ("full rationale in docs/DECISIONS.md"); not pulled in unless you or I explicitly read it |
| `CONTEXT.md` | repo root | 16 KB | Not auto-loaded (no evidence it's pulled in automatically; it's a `/domain-modeling` artifact referenced from CLAUDE.md) |
| `docs/agents/*.md` | repo | 16 KB | Not auto-loaded |
| Global `settings.json` | `~/.claude/settings.json` | small | Yes, but fixed/small (statusline command, 2 plugins enabled, effort level) |
| Global `settings.local.json` | `~/.claude/settings.local.json` | small | Yes, small permission allowlist only |
| Auto-memory (`MEMORY.md` + files) | `~/.claude/projects/-home-pelle-note-color/memory/` | tiny | Yes, but tiny (2 index entries) |
| Hooks | none found in either settings file | — | N/A |
| MCP servers | none found (no `.mcp.json` anywhere, nothing in settings) | — | N/A |
| Plugins | `clangd-lsp`, `mattpocock-skills` (global, enabled for all projects) | small, fixed | Yes — but this is a global choice affecting every project, not specific to note-color, and both surfaced as legitimately in-use (skills list shown this session). Not proposing to touch these. |

**Conclusion: the project's `CLAUDE.md` is the dominant, and essentially
only, addressable driver of this session's high initial token usage.**
Everything else (DECISIONS.md, CONTEXT.md, generated directories) is
either not auto-loaded or negligibly small.

## 3. The actual problem inside CLAUDE.md: mechanical duplication

`CLAUDE.md` isn't just long — a meaningful fraction of its length is
**exact, verbatim duplication** within single lines of its own "Files"
table, almost certainly from content being appended repeatedly over time
without deduplication (each markdown table row is one line, so this
duplication is invisible when skimming the rendered table).

Confirmed by direct inspection:

- **Line 332** (the `tests/` row of the Files table) is **48,943
  characters long — one single table cell** — and contains the same
  descriptive text for `test_prototypes_display.py`, `test_stats_display.py`,
  `test_score_editor_state.py`, `test_synth_layout.py`, `test_abc_export.py`,
  and others, **repeated 6 times verbatim** within that one line, plus a
  4th internal repeat of a `playback.py (map #24...)` paragraph.
- **Line 322** (the `main.py` row) is 15,128 characters and contains at
  least two verbatim-repeated phrases (`run_score_editor(path)` / `run_synth_tool(session=None)` descriptive blocks each appear twice).
- These are not the file's only long lines, but they are by far the
  largest and the ones with confirmed internal duplication.

This is exactly the kind of "clearly redundant... generated" content the
task rules permit cleaning up: it's not new information, not an
instruction, and not formatting — it's the same paragraph pasted multiple
times inside one cell, adding pure token cost with zero information gain.

## 4. Other things noticed (informational only — not proposed for action)

- **`build/` is tracked in git** (65 files, including a full duplicate
  `build/lib/*.py` mirror of old flat-module source and a 230 KB vendored
  ONNX file, `build/lib/vendor/basic_pitch/nmp.onnx` — identical to the
  one already properly vendored under `src/notecolor/convert/vendor/`).
  This looks like an accidental commit of a `python setup.py build`
  output directory. It adds ~1.3 MB to the repo and git history but is
  **not** loaded into session context automatically, so it doesn't affect
  token usage — flagging only because it's an unusual tracked/generated
  directory. Not proposing to touch it (touching tracked git content is
  higher-risk and the rules ask me to flag rather than act).
- **Stale agent worktrees**: `.claude/worktrees/agent-*` (6 directories,
  each a near-full checkout of the repo). `.claude/` is gitignored, so
  these cost no git/token overhead, but they are real disk usage from
  past agent runs. Not a token-usage item; not proposing any action
  (rules forbid deleting files, and I can't tell if any hold in-progress
  work).
- Large generated/runtime directories at repo root — `.venv` (2.3 GB),
  `acoustic_test_results/` (68 MB), `graphify-out/` (54 MB),
  `rhythm_test_results/` (2.1 MB), `__pycache__/`, several
  `session_log_*.jsonl` and `note_history_*.txt` dev-run artifacts (up to
  15 MB each) — **all already gitignored** and **none auto-loaded into
  context**. They explain disk usage, not token usage. No action needed
  or proposed.
- No MCP servers, no hooks, nothing else contributing meaningful
  session-start context was found.

## 5. Proposed changes, lowest risk to highest

### Change A — Deduplicate the two confirmed-duplicated lines in CLAUDE.md (RECOMMENDED)

- **What**: Edit `CLAUDE.md` lines 332 and 322 only, removing the
  verbatim-repeated copies of text within each line so each unique
  sentence/description appears once. No sentence is reworded, shortened,
  or removed except exact duplicate copies. No other line touched.
- **Estimated savings**: line 332 alone repeats its content roughly
  5–6×; collapsing it to one copy should shrink it from ~49 KB to
  roughly ~8–9 KB. Line 322's two duplicated phrases are a smaller
  saving (~1–3 KB). Combined, `CLAUDE.md` would shrink from **283 KB to
  roughly 235–245 KB** (a ~15–18% reduction), which — since this file
  loads in full on every session — saves roughly **10,000–12,000 tokens
  per session start** (rough estimate from character count; not
  independently measured against the tokenizer).
- **Risk**: Low-to-moderate. It edits the project's instruction file
  (`CLAUDE.md`), which the task rules place in a protected category
  ("preserve all existing instructions... unless clearly redundant").
  The duplication here is unambiguous (byte-identical repeated
  substrings), so no instructional content is lost — but because it's
  still an edit to the instructions file, **I'm treating this as
  requiring your explicit approval before touching it**, per the rules.
- **Requires your approval**: Yes.

### Change B — No-op items confirmed safe to leave alone

- `docs/DECISIONS.md` (302 KB): not auto-loaded, no savings available
  without deleting/shrinking real content, which the rules forbid.
  Leaving as-is.
- Generated/gitignored directories (`.venv`, `acoustic_test_results/`,
  `graphify-out/`, `build/`'s non-tracked siblings, `__pycache__/`,
  session logs, note-history dumps): none are auto-loaded; cleaning them
  would save disk, not tokens, and several of the rules explicitly
  forbid deleting files. **No change proposed.**

### Change C — Larger structural option (NOT recommended right now, flagged for awareness only)

- **What it would be**: Split CLAUDE.md's exhaustive per-module "Files"
  table (the single largest section, currently always-loaded) out into
  its own file (e.g. `docs/FILES.md`), referenced from CLAUDE.md by a
  short pointer paragraph — exactly the pattern already used for
  `docs/DECISIONS.md`. Only the pointer would load automatically; the
  detail would load only when actually needed.
- **Estimated savings**: This is the biggest lever available — plausibly
  a 5–8× reduction in what's auto-loaded (down to perhaps 30–50 KB of
  always-on instructions), since the Files table and the "Key design
  decisions" backlog make up most of the file's bulk.
  - Note: this is a plausible *upper bound* range, not a validated
    number.
- **Risk**: Higher. This is a genuine reorganization of a project
  convention (what's "always visible" vs. "looked up on demand"), not a
  mechanical dedup — it changes which content Claude sees by default in
  every future session, which could change how much the per-file
  rationale actually gets consulted. This clearly falls outside "safe,
  low-risk, no behavior change" and I'm not proposing to do it without a
  separate, explicit decision from you. Mentioning it only because it's
  the honest answer to "what would actually move the needle further."

## 6. What I'm asking approval for

Only **Change A** (deduplicating the two confirmed-duplicated lines,
322 and 332, in `CLAUDE.md`) is proposed for actual execution right now.
Everything else in this document is reporting, not a pending change.

If you approve Change A, I will:
1. Show you the exact before/after diff for both lines before committing
   to anything else.
2. Make only that edit — no other line, no other file.
3. Verify the file still parses/reads correctly (it's markdown, no
   build/lint/test step applies to it directly), and run the existing
   test suite (`pytest tests/`) anyway as a sanity check that nothing
   else was disturbed.
4. Tell you exactly how to revert (`git checkout -- CLAUDE.md`, since
   the file is currently clean/committed, or `git diff` beforehand if
   you'd rather review as a working-tree change before I commit).

## 7. Results (applied)

Both Change A and Change C were approved and executed.

**Change A — dedup.** Verified the "duplication" wasn't uniform copies of
one block; new content had been interleaved between repeats over time. So
instead of a blind regex pass, I located the exact repeated spans
programmatically (byte-for-byte matches only — nothing judged as "similar
enough," nothing merged or reworded) and removed only those:
- Line 332 (`tests/` row): split into its 119 comma-separated entries,
  kept the first occurrence of each exact entry, dropped 72 byte-identical
  repeats. 48,928 → 19,244 chars.
- Line 322 (`main.py` row): found one exact 3,690-char duplicated
  paragraph pair (the `run_synth_tool`/`run_score_editor` block was pasted
  twice with new content in between) and removed the second copy. 15,129
  → 11,439 chars.
- Confirmed no other line in the file has internal duplication (scanned
  all lines >2000 chars).
- **33,374 bytes removed, zero unique content lost.**

**Change C — restructure.** Moved the `## Files` table (line 265–332, the
per-module responsibility reference) out of `CLAUDE.md` into a new
`docs/FILES.md`, leaving a two-line pointer in its place — the same
pattern `docs/DECISIONS.md` already uses. The table itself is untouched
content-for-content (same rows, same text), just relocated; a short intro
paragraph was added to `docs/FILES.md` explaining why it's split out.

**Net result:**

| | Before | After |
|---|---|---|
| `CLAUDE.md` (always loaded every session) | 283,367 bytes | **127,585 bytes** |
| `docs/FILES.md` (loaded only when read) | — | 122,860 bytes |

That's a **55% cut** to what's auto-loaded at session start — roughly
**35,000–40,000 fewer tokens per session boot**, from this file alone
(rough character-based estimate, not independently measured against the
tokenizer). All 1,804 existing tests still pass (source code wasn't
touched, so this mainly confirms nothing else was disturbed by the edit).

**To revert either change:**
- Both together: `git checkout -- CLAUDE.md && rm docs/FILES.md` (nothing
  is committed yet, so this fully restores the original).
- Just Change C (keep the dedup, restore the inline table): would need to
  be reconstructed by hand since the pointer text replaced the table in
  place — ask if you want this and I'll do it precisely rather than via a
  git command, since the dedup already landed in the same file edit.

## 8. Round two — all three follow-up items (applied)

**1. `docs/DECISIONS.md` split into `docs/decisions/`.** It was one
302,561-byte file with 51 "## " sections and no internal links pointing
at specific sections from elsewhere in the repo (checked first — no
`DECISIONS.md#anchor` references exist anywhere). Split into 51 files
under `docs/decisions/`, one per decision (`01-python-numpy-not-a-...md`
etc.), same one-topic-per-file convention `docs/research/` already uses.
Verified byte-for-byte against the original (via `git show HEAD:...`)
that every section's body is preserved exactly — nothing reworded,
nothing dropped. `docs/DECISIONS.md` itself is now a 9,140-byte index
(title + one linked line per decision); every existing
`docs/DECISIONS.md` reference elsewhere in the repo (~40 files) still
resolves correctly since the file still exists at that path.

**2. `CLAUDE.md`'s "Key design decisions"/"Known limitations" sections
moved to `docs/SUMMARY.md`.** Worth flagging what makes this one
different from the Files-table move: this content was *already* the
condensed one-liner tier — each bullet already said "full rationale in
`docs/DECISIONS.md`" — so this wasn't deduplicating bulk, it was removing
the "glance at CLAUDE.md, get the one-line why" workflow from always-on
context entirely, in exchange for a smaller boot footprint. You'd asked
to really minimize, so I went ahead — 66,019 bytes (52% of CLAUDE.md at
the time) moved out, two-line pointer left behind. If in practice you
miss having the one-liners inline, this is the one of the three easiest
to partially undo (paste `docs/SUMMARY.md`'s bullets back in) without
touching the other two.

**3. `clangd-lsp` plugin checked, left enabled.** Inspected its install —
no skills, no hooks, no MCP config, just a README/LICENSE and a deferred
`LSP` tool registration for C/C++ file types. This is a Python project,
so it's never invoked here; its context cost is already negligible
(deferred tools cost near-zero until searched for). Disabling it would
not produce a measurable saving, so I left it on. `mattpocock-skills`
(the other enabled plugin) was also checked and left alone — it provides
real skills this project's own CLAUDE.md documents using
(`code-review`, `domain-modeling`).

### Final numbers

| File | Original | Now |
|---|---|---|
| `CLAUDE.md` (always loaded) | 283,367 bytes | **61,626 bytes** |
| `docs/DECISIONS.md` (index, loaded on demand) | 302,561 bytes | 9,140 bytes |
| `docs/FILES.md` (loaded on demand) | — | 122,860 bytes |
| `docs/SUMMARY.md` (loaded on demand) | — | 66,666 bytes |
| `docs/decisions/*.md` (51 files, loaded one at a time on demand) | — | 416 KB total |

**`CLAUDE.md`, the only one of these actually paid on every session boot,
is down 78% — from 283KB to 62KB.** All 1,804 tests still pass at every
step (no source code touched). Nothing is committed yet.

**To revert:** `git checkout -- CLAUDE.md docs/DECISIONS.md && rm -rf
docs/FILES.md docs/SUMMARY.md docs/decisions/` restores everything to the
original single-file state exactly, since nothing has been committed.
