"""End-to-end runner for the MIDI-export feasibility prototype -- see
README.md and docs/research/midi-export-feasibility.md.

Builds `sample_result.SAMPLE_RESULT` (a synthesized `batch_transcribe.
TranscriptionResult`) into a single `music21.stream.Score` via
`midi_writer.build_score()` (which reuses `score_writer.py`'s own private
helpers -- see that module's docstring), writes the *same* Score to both
`.musicxml` (score_writer.py's existing format) and `.mid` (this
prototype's new one), then inspects both real output files to report,
concretely:

1. Does `Score.write("midi", ...)` succeed at all against this app's real
   object graph?
2. Structural survival: note count, pitches, staff/track split, key
   signature, time signature, tempo.
3. Color survival (or lack of it) -- this app's one MusicXML-driving
   requirement (`Note.style.color`) that MIDI has no native concept for.
4. A side-by-side against the MusicXML round trip `score_writer.py`
   already established, so the comparison is apples-to-apples.

Run: `.venv/bin/python prototypes/midi-export/run_demo.py`
(or `python3 prototypes/midi-export/run_demo.py` if `.venv/bin/python` isn't
on this checkout, so long as `python3` resolves to the project's venv).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[2]))  # repo root

from music21 import chord as m21chord, converter, midi as m21midi

from midi_writer import build_score
from sample_result import SAMPLE_RESULT

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def _print_header(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    musicxml_path = OUTPUT_DIR / "demo.musicxml"
    midi_path = OUTPUT_DIR / "demo.mid"

    _print_header("1. Building the Score (score_writer.py's own helpers, reused)")
    score = build_score(SAMPLE_RESULT)
    print(f"Built a Score with {len(score.parts)} parts (expect 2: treble, bass).")

    _print_header("2. Writing both formats from the SAME in-memory Score")
    score.write("musicxml", fp=str(musicxml_path))
    print(f"MusicXML written: {musicxml_path} ({musicxml_path.stat().st_size} bytes)")
    score.write("midi", fp=str(midi_path))
    print(f"MIDI written:      {midi_path} ({midi_path.stat().st_size} bytes)")
    print("Both writes succeeded with no exception -- confirms Score.write(\"midi\", ...)")
    print("works against this app's real two-staff/chord/color object graph, not just a")
    print("toy example.")

    _print_header("3. Re-parsing both files back with music21.converter.parse()")
    parsed_xml = converter.parse(str(musicxml_path))
    parsed_mid = converter.parse(str(midi_path))

    xml_notes = list(parsed_xml.recurse().notes)
    mid_notes = list(parsed_mid.recurse().notes)
    print(f"MusicXML round-trip: {len(xml_notes)} note/chord elements parsed back.")
    print(f"MIDI round-trip:     {len(mid_notes)} note/chord elements parsed back.")

    def flat_pitch_classes(elements):
        pcs = []
        for el in elements:
            if isinstance(el, m21chord.Chord):
                pcs.extend(sorted(p.pitchClass for p in el.pitches))
            else:
                pcs.append(el.pitch.pitchClass)
        return sorted(pcs)

    xml_pcs = flat_pitch_classes(xml_notes)
    mid_pcs = flat_pitch_classes(mid_notes)
    print(f"MusicXML pitch classes (flattened, sorted): {xml_pcs}")
    print(f"MIDI pitch classes (flattened, sorted):      {mid_pcs}")
    print(f"Match: {xml_pcs == mid_pcs}")

    _print_header("4. Color survival check (Note.style.color)")
    xml_colors = [getattr(el.style, "color", None) for el in xml_notes]
    mid_colors = [getattr(el.style, "color", None) for el in mid_notes]
    print(f"MusicXML note colors: {xml_colors}")
    print(f"MIDI note colors:     {mid_colors}")
    if any(c is not None for c in xml_colors) and all(c is None for c in mid_colors):
        print("CONFIRMED: color survives in MusicXML, is unconditionally dropped in MIDI.")
        print("This is a real, unavoidable format limitation -- Standard MIDI Files have no")
        print("per-note color concept at all (no such meta-event/controller exists in the")
        print("spec), not a music21 writer gap.")
    else:
        print("UNEXPECTED: color survival did not match the expected pattern -- investigate.")

    _print_header("5. Key/time signature/tempo survival (raw MIDI event inspection)")
    mf = m21midi.MidiFile()
    mf.open(str(midi_path), "rb")
    mf.read()
    mf.close()
    event_names_seen = set()
    tempo_event = None
    for track in mf.tracks:
        for event in track.events:
            name = repr(event).split("MidiEvent ", 1)[-1].split(",", 1)[0] if "MidiEvent" in repr(event) else None
            if name:
                event_names_seen.add(name)
            if name == "SET_TEMPO":
                tempo_event = event
    print(f"Meta/channel event types found across all MIDI tracks: {sorted(event_names_seen)}")
    print(f"Number of tracks: {len(mf.tracks)} (expect 3: one tempo/meta track + 2 staff tracks)")
    if tempo_event is not None:
        # SET_TEMPO data is 3 bytes, big-endian microseconds-per-quarter-note.
        usec_per_quarter = int.from_bytes(tempo_event.data, "big")
        bpm = 60_000_000 / usec_per_quarter
        print(f"Tempo event found: {usec_per_quarter} usec/quarter -> {bpm:.1f} bpm "
              f"(expected {SAMPLE_RESULT.bpm} bpm from include_tempo_mark=True)")

    key_sigs_xml = list(parsed_xml.recurse().getElementsByClass("KeySignature"))
    key_sigs_mid = list(parsed_mid.recurse().getElementsByClass("KeySignature"))
    print(f"MusicXML key signatures found on re-parse: {[ks.sharps for ks in key_sigs_xml]}")
    print(f"MIDI key signatures found on re-parse:      {[ks.sharps for ks in key_sigs_mid]}")

    time_sigs_xml = list(parsed_xml.recurse().getElementsByClass("TimeSignature"))
    time_sigs_mid = list(parsed_mid.recurse().getElementsByClass("TimeSignature"))
    print(f"MusicXML time signatures: {[(t.numerator, t.denominator) for t in time_sigs_xml]}")
    print(f"MIDI time signatures:      {[(t.numerator, t.denominator) for t in time_sigs_mid]}")

    _print_header("6. Staff/part structure survival")
    print(f"MusicXML parts: {len(parsed_xml.parts)}")
    print(f"MIDI parts (tracks with note content): "
          f"{sum(1 for p in parsed_mid.parts if list(p.recurse().notes))}")

    _print_header("Summary")
    print("See docs/research/midi-export-feasibility.md for the full write-up.")
    print(f"Output files left in {OUTPUT_DIR} for manual inspection (e.g. import demo.mid")
    print("into a real DAW/notation program to confirm it opens cleanly).")


if __name__ == "__main__":
    main()
