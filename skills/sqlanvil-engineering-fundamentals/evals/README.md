# Evals for sqlanvil-engineering-fundamentals

`evals.json` holds the test prompts and their assertions in the skill-creator schema
(`skill-creator` plugin, `claude-plugins-official`). Runs, transcripts, compiled graphs and
grades go in `<repo>/sqlanvil-engineering-fundamentals-workspace/` (gitignored, a sibling of
`skills/` so the frontmatter lint never walks it), never here.

The grader is the engine. Each prompt asks for a small sqlanvil project; `grade.py` compiles
what the agent produced with the installed `sqlanvil` CLI and settles every `[auto:...]`
assertion from the compiled graph (`compile --json`) and the source files. The pin in SKILL.md
is the version to grade with (`npm i -g @sqlanvil/cli@latest`): several deltas only compile on
the current release (named index methods, the `supabase:` block, actions.yaml Supabase entries).

## Running an iteration

1. `python3 setup_iteration.py N` creates `iteration-N/eval-*/{with_skill,without_skill}/run-1/outputs/`
   and prints each run's prompt with `<OUTDIR>` filled in.
2. Spawn one subagent per run, all in the same turn. The with-skill run is told to read
   `../SKILL.md` first and follow it; the baseline is told not to invoke any skill or read any
   SKILL.md (the `sqlanvil-toolkit` plugin would otherwise auto-load the same skill). Both are told
   the `sqlanvil` CLI is installed: a real user has it too, and the question is whether the skill
   saves the trial-and-error, not whether the CLI exists. Each writes its project and a
   `transcript.md` (commands, key output, final message verbatim) into `outputs/`.
3. Save each subagent's `total_tokens` / `duration_ms` to `run-1/timing.json` as the
   notifications arrive; they are not stored anywhere else.
4. Grade: `python3 grade_handover.py <run-dir>...` first (it settles the one untagged
   assertion, the hand-over, from the transcript's final message using each eval's `handover`
   regexes and writes `run-1/manual_grades.json`), then `python3 grade.py <run-dir>` for every
   run. That writes `run-1/grading.json` (`expectations[] = {text, passed, evidence}` +
   `summary`) and `run-1/compiled.json`. Any assertion still `passed: null` needs a hand-written
   entry in `manual_grades.json` (`{"<assertion text>": {"passed": true, "evidence": "..."}}`).
5. From the skill-creator skill directory:
   `python -m scripts.aggregate_benchmark <workspace>/iteration-N --skill-name sqlanvil-engineering-fundamentals`
   then `python eval-viewer/generate_review.py <workspace>/iteration-N --skill-name sqlanvil-engineering-fundamentals --benchmark .../benchmark.json`.

## What the four prompts test

| eval | warehouse | exercises | the failure it is meant to catch |
|---|---|---|---|
| pg-events-partition-gin-matview | postgres | deltas 1-7, 10, 11, 14 | `CREATE INDEX` / `PARTITION BY` hand-rolled in `post_operations`; `partitionBy`/`clusterBy`; `;` between the function and its call; nested or BigQuery-shaped credentials |
| supabase-incremental-upsert-rls | supabase | deltas 2, 8, 9, 13, 14 | `config { type: "rlsPolicy" }` in a `.sqlx`; bare `ADD PRIMARY KEY` that re-runs on every append; `uniqueKey` confused with `assertions.uniqueKey` |
| mysql-mariadb-fulltext-procedure | mysql | the MySQL inversions | a `postgres:` block on a mysql model; `DELIMITER` around the procedure; a hand-added unique index for the merge; double-quoted identifiers; Postgres matview options |
| supabase-bigquery-source-runner-extract | supabase + bigquery source | delta 15 | a hand-rolled `wrappers` / `CREATE SERVER` / foreign table; `columnTypes` in BigQuery types; key material in `workflow_settings.yaml`; `mode: fdw` on a database that can't take extensions |

Every check in `grade.py` is parameterised (`python3 grade.py --list-checks`), so a new prompt
usually needs no new code: tag its assertions `[auto:<check> args...]` and the existing checks
settle them. Add a prompt when a real request exposed a failure the skill should have prevented,
and write its assertions so the compiled graph can settle them: an index that is really declared
in config appears as `postgres.indexes`, a `when(!incremental())` guard appears as the statement
being in `postOps` but not `incrementalPostOps`.
