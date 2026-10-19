# File imports/exports and Python script actions

## `type: "import"` (≥1.12) and `type: "export"` (≥1.8)

Config-only actions that move **files** across the warehouse boundary (Parquet/CSV/JSON; local
paths or `s3://`/`gs://` URIs — bucket creds go under a `storage:` map in `.df-credentials.json`):

```sqlx
config { type: "import", dataset: "oa_ext", name: "openaddresses_us",
         import: { location: "data/sample.csv", format: "csv", overwrite: true } }
```

- **`import`** loads a file into a warehouse table — a *producer*, `${ref()}`-able like any
  source (on Postgres/Supabase via the runner-side DuckDB bridge; BigQuery uses native
  `LOAD DATA`, gs:// only; MySQL throws). `location` is the verbatim source path/glob/URI — NOT a
  derived filename; `overwrite` defaults true (drop+create), `false` appends. No transform on the
  way in — shape it downstream.
- **`export`** writes a query's result to a file — a terminal *sink* (nothing can `ref()` it).
- `validate` marks imports SKIPPED (file columns unknown pre-run) and downstream models BLOCKED —
  expected, not a bug. Hosted SQLAnvil Cloud rejects *local* paths at compile (ephemeral runner
  disk) — use object-store URIs there.

## Python script actions (`python:` in actions.yaml, ≥1.20)

Execution-time Python steps as DAG nodes — the staging/glue slot (download, unzip, API call)
before an `import`. Declared in `definitions/actions.yaml` (NOT a new file extension; NOT
compile-time like `.js` — compile never runs Python):

```yaml
actions:
  - python:
      name: fetch_data
      file: loader/fetch_data.py             # plain .py, path from project root
      requirements: loader/requirements.txt   # optional; VALIDATED, never installed
      pythonVersion: ">=3.11"                 # optional PEP 440 specifier
      venv: .venv                             # optional; that venv's interpreter runs it
      dependencies: ["upstream_action"]
```

- Contract: cwd = project dir; env `SA_VARS` (vars as JSON) + `SA_ACTION_NAME`; `args:` →
  `sys.argv[1:]`; exit 0 = success; 30-min default timeout (`timeoutMillis`).
- **No warehouse credentials are injected** — the script stages FILES; a downstream
  `type: "import"` with `dependencyTargets: [{name: "fetch_data"}]` loads them. Never have the
  script write to the warehouse itself.
- The user owns the environment (pip/uv install is their job); `sqlanvil validate` verifies it —
  never installs. A failing script BLOCKs dependents.
- Hosted SQLAnvil Cloud rejects script actions at compile — local CLI / BYO CI only.
- `python:` is sugar for the language-neutral `script: { language: "python", ... }` (proto
  fields: `filename`, `depsFile`, `runtimeVersion`, `envRoot`).
