#!/usr/bin/env python3
"""
Lay out one eval iteration from evals.json, in the shape skill-creator's aggregator
and viewer read:

    <repo>/<skill>-workspace/iteration-N/eval-<id>-<name>/<config>/run-1/outputs/

    python3 setup_iteration.py 1                    # configs: with_skill, without_skill
    python3 setup_iteration.py 2 --configs with_skill old_skill

The workspace sits at the repo root (a sibling of skills/), not inside skills/, so the
frontmatter lint that walks skills/*/SKILL.md never sees it; it is gitignored.
Override with EVAL_WORKSPACE=/some/dir.

Writes eval_metadata.json at the eval directory (for aggregate_benchmark.py) and in
each run directory (for generate_review.py and grade.py), and prints each run's prompt
with <OUTDIR> replaced by that run's outputs/ path, ready to paste into a subagent brief.
"""

import argparse
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
WORKSPACE = Path(os.environ.get("EVAL_WORKSPACE") or SKILL.parent.parent / f"{SKILL.name}-workspace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("iteration", type=int)
    ap.add_argument("--configs", nargs="+", default=["with_skill", "without_skill"])
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--only", nargs="*", type=int, help="eval ids to lay out (default: all)")
    args = ap.parse_args()

    evals = json.loads((HERE / "evals.json").read_text())["evals"]
    it = WORKSPACE / f"iteration-{args.iteration}"
    for e in evals:
        if args.only and e["id"] not in args.only:
            continue
        eval_dir = it / f"eval-{e['id']}-{e['name']}"
        meta = {"eval_id": e["id"], "eval_name": e["name"], "prompt": e["prompt"], "assertions": e["assertions"]}
        eval_dir.mkdir(parents=True, exist_ok=True)
        (eval_dir / "eval_metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
        for cfg in args.configs:
            for r in range(1, args.runs + 1):
                run = eval_dir / cfg / f"run-{r}"
                out = run / "outputs"
                out.mkdir(parents=True, exist_ok=True)
                run_meta = dict(meta, prompt=e["prompt"].replace("<OUTDIR>", str(out)))
                (run / "eval_metadata.json").write_text(json.dumps(run_meta, indent=2) + "\n")
                print(f"== {cfg} run-{r}: {run}\n{run_meta['prompt']}\n")
    print(f"workspace: {it}")


if __name__ == "__main__":
    main()
