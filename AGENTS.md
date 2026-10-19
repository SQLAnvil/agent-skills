# Contributing / maintaining this repo

This repo is the **single canonical source** of the public SQLAnvil skill. Do not treat any
other copy as authoritative — the engine repo's `AGENTS.md` is the *contributor-facing*
authoring guide and serves a different scope (it assumes a checkout of the engine).

## Release sync (mandatory)

On every SQLAnvil engine release, update `skills/sqlanvil-engineering-fundamentals/SKILL.md`
in the same pass as the engine's `AGENTS.md` and the sqlanvil-com docs:

1. Bump both `sqlanvilCoreVersion:` pins (workflow_settings examples).
2. Add/adjust deltas for anything the release changed — this is a **content review against
   the release notes**, not a blind copy from the engine AGENTS.md (the two files serve
   different scopes and phrasings).
3. Keep the frontmatter `description` stable unless the skill's trigger surface genuinely
   changed — it's what agents match on.

## Layout of the fundamentals skill

- `SKILL.md` holds the deltas that bite and the hand-over checklist — the part every
  authoring task pays for. Keep it lean (it is loaded whole on every trigger).
- `references/` holds what is read only for a specific job: `mysql.md`,
  `cross-warehouse-sources.md`, `migrating-from-dataform.md`, `files-and-scripts.md`,
  `cli-and-environments.md`, `older-pins.md`. A release note that only matters on an old pin
  goes in `older-pins.md`, not SKILL.md.
- `evals/` holds the test prompts and `grade.py`, which compiles each produced project with
  the installed CLI and grades the deltas from the compiled graph. Run an iteration after any
  substantive edit (`evals/README.md`); runs land in the gitignored
  `../../sqlanvil-engineering-fundamentals-workspace/`.

## Conventions

- One directory per skill under `skills/`, each containing a `SKILL.md` with `name` (must
  match the directory) and `description` frontmatter, per the Agent Skills spec
  (https://agentskills.io).
- The skill must stay **self-contained**: no references to private repos, personal skill
  ecosystems, or files a third-party user won't have.
- CI lints frontmatter on every push/PR (`.github/workflows/lint.yml`).
