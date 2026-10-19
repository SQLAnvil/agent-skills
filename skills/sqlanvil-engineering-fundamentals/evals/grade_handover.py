#!/usr/bin/env python3
"""
Grade the hand-over assertion of a run from its transcript's final message.

    python3 grade_handover.py <run-dir> [<run-dir> ...]

Each eval in evals.json carries a `handover` list of [label, regex] pairs. The run's
transcript must contain a "## Final message" heading (the subagent brief asks for it); the
text after it is the final message. Every regex must match it (case-insensitive) for the
assertion to pass. Writes <run-dir>/manual_grades.json in the shape grade.py reads, keyed by
the untagged assertion text, and prints the verdict. Run grade.py afterwards.

Why regexes and not judgement: the hand-over is the one assertion the engine cannot settle,
and it was the most-failed assertion in iteration 1. Spelling out what a good hand-over
must contain (the run command, the gitignored creds, the warehouse caveat) keeps the grade
the same from iteration to iteration.
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAG = re.compile(r"^\[auto:")


def final_message(run: Path) -> str:
    t = (run / "outputs" / "transcript.md").read_text(encoding="utf-8", errors="replace")
    if "## Final message" in t:
        return t.split("## Final message", 1)[1]
    return t[-4000:]  # no heading: judge the tail, which is where agents put it anyway


def main():
    evals = {e["id"]: e for e in json.loads((HERE / "evals.json").read_text())["evals"]}
    rc = 0
    for arg in sys.argv[1:]:
        run = Path(arg).resolve()
        meta = json.loads((run / "eval_metadata.json").read_text())
        e = evals[meta["eval_id"]]
        manual = [a for a in meta["assertions"] if not TAG.match(a)]
        if not (run / "outputs" / "transcript.md").exists():
            print(f"SKIP {run}: no outputs/transcript.md")
            rc = 1
            continue
        fm = final_message(run)
        hits = [label for label, rx in e.get("handover", []) if re.search(rx, fm, re.I | re.S)]
        miss = [label for label, _ in e.get("handover", []) if label not in hits]
        passed = not miss
        ev = ("final message has " + ", ".join(hits)) if passed else (
            "final message lacks " + ", ".join(miss) + (f" (has {', '.join(hits)})" if hits else ""))
        grades = {a: {"passed": passed, "evidence": ev} for a in manual}
        (run / "manual_grades.json").write_text(json.dumps(grades, indent=2) + "\n")
        print(f"{'PASS' if passed else 'FAIL'} {run.parent.parent.name}/{run.parent.name}: {ev}")
        rc |= 0 if passed else 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
