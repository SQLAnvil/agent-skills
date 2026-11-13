# CLI, validation, artifacts and environments

Every command and flag: https://sqlanvil.com/docs/reference/cli/ (or `sqlanvil help <command>`).
Install with `npm i -g @sqlanvil/cli` (needs Node 20.19+ or 22.12+).

```bash
sqlanvil init      <projectDir> --warehouse postgres   # or supabase/mysql — scaffolds workflow_settings.yaml, a .df-credentials.json template, AGENTS.md (BigQuery is the default and needs a GCP project; the location is optional since 1.38.0)
sqlanvil init      <projectDir> --warehouse supabase --bare   # ≥1.23: skip the sample project. --interactive is a guided human Q&A; agents use the flags
sqlanvil compile   <projectDir>                        # no warehouse needed; --json prints the graph
sqlanvil run       <projectDir> --credentials <projectDir>/.df-credentials.json
sqlanvil run       <projectDir> --credentials ... --full-refresh
sqlanvil run       <projectDir> --credentials ... --actions <name> --include-deps
sqlanvil validate  <projectDir> --credentials ...      # EXPLAIN-validate the whole DAG without executing (PG/Supabase/MySQL; BigQuery via dry-run)
sqlanvil test      <projectDir> --credentials ...
sqlanvil introspect <connection> <schema.table> --output definitions/sources/<name>.sqlx
sqlanvil migrate-dataform <srcDir> <outDir>            # see migrating-from-dataform.md
```

## `validate` / `run --dry-run` (≥1.9)

Walks the DAG in dependency order, `EXPLAIN`-checks each model against the live warehouse in a
throwaway shadow schema (empty stubs let downstream `${ref()}`s resolve), and reports **PASS /
FAILURE / BLOCKED** (blocked = only an upstream failed) / SKIPPED (operations, imports).
`run --dry-run` on Postgres/Supabase/MySQL *validates* — it does not execute. Any FAILURE/BLOCKED
exits non-zero. Python script actions (≥1.20) get an env check instead of EXPLAIN — interpreter vs
`pythonVersion`, requirements vs installed packages, syntax — without executing the script.

## `run --json` and `run --build-only` (1.38.0+)

`run --json` prints the run's **result** (a `RunResult`: overall status, each action's status,
tasks and timing) on stdout, and exits 1 if anything failed; progress and warnings go to stderr.
To see what *would* run without running it, use `run --build-only` (exits 0; `--build-only --json`
prints the execution graph). On Postgres/Supabase/MySQL, `run --dry-run --json` still prints the
`validate` results. Before 1.38.0, `run --json` without `--dry-run` printed a note, ran nothing and
exited 0, so never use it as a CI gate on an older pin.

## `run --graph <file>` (≥1.17)

Executes a stored `compile --json` output directly — no compile, no project source needed (bare
dir + credentials works). What runs is exactly what was compiled (environment overrides are baked
in — `--environment` with `--graph` is rejected; selection flags still apply). This is the
release-artifact pattern: compile once, run the identical graph later/elsewhere.

## Queryable artifacts

`compile`/`run` write Parquet artifacts under `<projectDir>/target/` (gitignore it).
`sqlanvil query "<sql>"` runs SQL over them (views: `actions`, `dependencies`, `columns`, `runs`),
`sqlanvil inspect` prints a project/run summary, `sqlanvil docs` renders a self-contained HTML
catalog to `target/docs/index.html`. All warehouse-agnostic (no connection needed). Full guide:
https://sqlanvil.com/docs/guides/metadata/

## Pinned compiles skip gitignored files (1.37.3+)

With `sqlanvilCoreVersion` set, `compile` installs that core into a temporary **copy** of the
project and compiles the copy. The copy skips what the root `.gitignore` excludes, except
`definitions/`, `includes/`, `workflow_settings.yaml` and `.npmrc` (and anything they reach through
symlinks); `.git` and `node_modules` are never copied. So SQL that `actions.yaml` points at, or a
helper a definition `require()`s, must not sit in a gitignored directory outside `definitions/` or
`includes/`. If it must, re-include it in a root `.sqlanvilignore` (`.gitignore` syntax, read
second): `!/queries/`. The failing compile prints a `Note:` naming the excluded path. Full page:
https://sqlanvil.com/docs/troubleshooting/#a-pinned-compile-cant-find-a-gitignored-file

## Named environments (`--environment <name>`)

Define dev/staging/prod in an `environments:` block in `workflow_settings.yaml`; each bundles
non-secret overrides + a pointer to a gitignored credentials file:

```yaml
environments:
  dev:  { schemaSuffix: dev,  credentials: .df-credentials.dev.json }
  prod: { defaultDatabase: prod_db, vars: { region: us-prod }, credentials: .df-credentials.prod.json }
```

`sqlanvil run . --environment prod` loads prod's overrides + its credentials file (works on
compile/run/test). Precedence: **explicit CLI flag > environment > workflow_settings defaults**
(`vars` merge per-key). Each env's `credentials:` is a path to a **gitignored**
`.df-credentials*.json` file — secrets never go in `workflow_settings.yaml`. `--schema-suffix`
stays the low-level primitive.
