"""SessionPlayer -- reads a `.jsonl` session log back (as written by
SessionRecorder) and replays it as a terminal printout: the sequence of
note/barline events in timestamp order, either dumped instantly or paced
out with real delays between events (`--realtime`/`speed=`).

Deliberately minimal, per this prototype's own scope note: a real
`virtualnote replay session.jsonl` (feature idea 1 in
docs/research/notation-and-feature-ideas.md) would re-drive
`TabDisplay.push`/`push_notes`/`push_barline`/`finalize_duration` from
these same timestamps instead of live audio; this prototype only proves
the log is readable and its timeline reconstructible, which is the part
that needed demonstrating.
"""

import json
import time


class SessionPlayer:
    def __init__(self, path):
        self.path = path

    def load_events(self):
        """All events, sorted by timestamp. SessionRecorder already
        appends in time order for a single live session, but sorting here
        keeps replay correct even against a hand-edited or concatenated
        log."""
        events = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        events.sort(key=lambda e: e["t"])
        return events

    def replay(self, speed=1.0, realtime=False, out=print):
        """Print every event in order. `realtime=True` sleeps between
        events proportional to their real timestamp gap / `speed` (2.0 =
        twice as fast); `realtime=False` (default) dumps the whole
        timeline instantly, which is what the demo/tests use so a run
        doesn't take as long as the original performance."""
        events = self.load_events()
        last_t = 0.0
        for event in events:
            if realtime:
                gap = (event["t"] - last_t) / max(speed, 1e-6)
                if gap > 0:
                    time.sleep(gap)
                last_t = event["t"]
            out(self._format_event(event))
        return events

    @staticmethod
    def _format_event(event):
        t = event["t"]
        if event.get("kind") == "barline":
            return f"[{t:7.2f}s]  |  barline"
        chord_tag = f"  chord={event['chord_name']}" if event.get("chord_name") else ""
        return (
            f"[{t:7.2f}s]  note {event['label']:<4} "
            f"dur={event['duration_class']:<15} "
            f"({event['duration_seconds']:.3f}s)"
            f"{chord_tag}"
        )


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: session_player.py <session.jsonl>")
        raise SystemExit(1)
    SessionPlayer(sys.argv[1]).replay()
