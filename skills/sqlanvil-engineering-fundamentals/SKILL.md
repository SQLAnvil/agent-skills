---
name: sqlanvil-engineering-fundamentals
description: Use when writing or editing a sqlanvil data project (.sqlx models, workflow_settings.yaml, .df-credentials.json) targeting PostgreSQL, Supabase, or MySQL/MariaDB - corrects Dataform/BigQuery priors that produce wrong config blocks, credentials, DDL, separators, and CLI commands in the sqlanvil fork
license: Apache-2.0
metadata:
  source: https://github.com/SQLAnvil/sqlanvil
---

# sqlanvil Engineering Fundamentals

## Overview

**sqlanvil is a fork of Dataform repositioned for PostgreSQL, Supabase, and MySQL/MariaDB.** Your Dataform/BigQuery instincts are *mostly* right — `.sqlx` files, `config {}` blocks, `${ref()}`, `${self()}`, declarations, assertions, tags, incremental tables, `pre_operations`/`post_operations` all work the same. **This skill is the delta**: the specific places where assuming Dataform/BigQuery produces broken sqlanvil code.

**Core principle:** When the warehouse is Postgres/Supabase, reach for the **`postgres: {}` config block** and idiomatic Postgres — never BigQuery options or hand-rolled DDL workarounds. **MySQL/MariaDB is different** — it has a deliberately smaller surface (no options block, no matviews) and several Postgres rules *invert*; see the dedicated MySQL section below before authoring a `warehouse: mysql` project.

**Carry-over fundamentals:** standard Dataform engineering discipline applies unchanged — write assertions/tests first, layer the DAG (sources → intermediate → outputs), use `${ref()}` for every table reference (never hardcoded paths), and document every table with `columns: {}`. This skill covers only the deltas.

**Authoritative config reference:** `protos/configs.proto` in the sqlanvil repo (`PostgresOptions`, `PostgresConnection`, `ConnectionConfig`, `TableConfig`, `IncrementalTableConfig`, `ViewConfig`, `DeclarationConfig`). When unsure of a field, read the proto — it is the source of truth.

## When to Use

- Writing any `.sqlx` model, `workflow_settings.yaml`, or `.df-credentials.json` for a sqlanvil project on Postgres/Supabase **or MySQL/MariaDB**
- Adding indexes, partitioning, storage options, materialized views, or stored procedures
- Reading a source table from **another warehouse** (BigQuery, a second Postgres, or MySQL/MariaDB) — named connections + `introspect`
- Loading a file into the warehouse (`type: "import"`) or exporting query results to files (`type: "export"`)
- Staging/glue steps in Python (`python:` script actions in actions.yaml, ≥1.20)
- Translating an existing Dataform/BigQuery project to sqlanvil/Postgres
- Any time you're about to write `bigquery: {}`, `partitionBy`, `dataform.json`, `method: "btree"`, or `;` between statements — STOP and check the deltas

## The Deltas That Bite (read before writing anything)

### 1. Project config: `workflow_settings.yaml`, warehouse is Postgres
```yaml
warehouse: postgres            # flat string ("postgres" or "supabase") — NOT nested
defaultDataset: public         # the Postgres SCHEMA
defaultAssertionDataset: sqlanvil_assertions
sqlanvilCoreVersion: 1.24.0    # sqlanvil's OWN SemVer line (NOT dataformCoreVersion); pin the current release
vars:
  someVar: value
```
**Drop** `defaultProject` and `defaultLocation` — they are BigQuery-only. (`dataform.json` is the legacy upstream name; sqlanvil uses `workflow_settings.yaml`.)

### 2. Connection: `.df-credentials.json` — flat `PostgresConnection`
The filename is literally `.df-credentials.json` (the fork kept upstream's name; it's the default, override with `--credentials`). The JSON is a **flat** `PostgresConnection` — **not** nested under a `"postgres"` key, **not** the BigQuery shape:
```json
{
  "host": "localhost",
  "port": 5432,
  "database": "postgres",
  "user": "postgres",
  "password": "password",
  "sslMode": "require",
  "defaultSchema": "public"
}
```
Field names exactly: `host port database user password sslMode defaultSchema` — **not** `username`/`databaseName`/`ssl`. Gitignore it. Supabase: `host: db.<ref>.supabase.co`, port 5432 (or 6543 pooler), `sslMode: "require"`.

### 3. Storage, indexes, partitioning are FIRST-CLASS — never hand-roll DDL
Use the `postgres: {}` block on `type: "table"` and `type: "incremental"`. **Do not** create indexes or set fillfactor via `post_operations` raw DDL.
```sqlx
config {
  type: "table",
  postgres: {
    fillfactor: 80,
    unlogged: false,
    tablespace: "fast_ssd",
    indexes: [
      { name: "idx_email", columns: ["email"], unique: true },
      { name: "idx_props", columns: ["props"], method: 2, opclass: "jsonb_path_ops" }
    ]
  }
}
```
**Index `method` is a NUMERIC ENUM, not a string:** `BTREE=0, HASH=1, GIN=2, GIST=3, BRIN=4`. `method: "btree"` fails the config type check (the parser uses protobufjs `create()`). Omit `method` for btree (default 0).
Index fields: `name`, `columns[]`(array), `method`(int), `where`(partial predicate), `unique`(bool), `include[]`(array, covering), `opclass`(**single string**, applied to every indexed column — `opclass: "gin_trgm_ops"`, **not** an array).

### 4. Native partitioning via `postgres.partition` (not `partitionBy`/`clusterBy`)
```sqlx
postgres: {
  partition: {
    kind: 0,                                   // RANGE=0, LIST=1, HASH=2 (numeric enum)
    columns: ["order_date"],
    partitions: [
      { name: "y2024", values: "FROM ('2024-01-01') TO ('2025-01-01')" }
    ],
    includeDefault: true
  }
}
```
`values` is the raw `FOR VALUES` body matching `kind`. There is no `clusterBy` — use `indexes` instead.

### 5. Materialized views: `type: "view", materialized: true`
Emits `CREATE MATERIALIZED VIEW`. Default = **drop + recreate every run** (which also picks up definition changes). Set Postgres options directly in the view config via `postgres: {}`:
```sqlx
config {
  type: "view",
  materialized: true,
  postgres: {
    refreshPolicy: "on_dependency_change",   // in-place REFRESH instead of drop+recreate
    noData: true,                            // CREATE ... WITH NO DATA (empty until first refresh)
    indexes: [{ name: "idx_mv_id", columns: ["id"], unique: true }]
  }
}
```
`refreshPolicy: "on_dependency_change"` runs `REFRESH MATERIALIZED VIEW` in place instead of drop+recreate — but in-place refresh does **not** pick up definition (SQL) changes; omit `refreshPolicy` for the safe drop+recreate default.

### 6. Statement separator is `---`, never `;`
Three dashes on their own line separate statements in `operations`, `pre_operations`, `post_operations`. sqlanvil never splits on `;`, so PL/pgSQL `$$ ... ; ... $$` bodies survive intact. A `table`/`view` model body is exactly ONE `SELECT` — no `---`.

### 7. Procedures / functions / triggers / extensions / grants → `type: "operations"`
No dedicated action type; the operations generator is dialect-agnostic.
```sqlx
config { type: "operations", hasOutput: false }
CREATE OR REPLACE FUNCTION marts.recalc() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  -- body; semicolons here are fine
END;
$$
---
CALL marts.recalc()
```

### 8. Two different `uniqueKey`s — do not confuse them
- Top-level `uniqueKey: ["id"]` on an **incremental** = the upsert/merge key (Postgres `INSERT ... ON CONFLICT`). Controls incremental write behavior.
- `assertions: { uniqueKey: ["id"] }` = generates a uniqueness **assertion** action. A data-quality check.

They are independent and can coexist.

### 9. Primary/foreign keys / one-time DDL on incrementals → wrap in `when(!incremental())`
An **unwrapped** `pre_operations`/`post_operations` block runs on **every** run of an incremental — the initial create *and* every append (the block is compiled into both the create-path ops and the incremental-path ops). So a bare `ALTER TABLE ... ADD PRIMARY KEY` (or `ADD CONSTRAINT ... FOREIGN KEY`) errors on the second run (`multiple primary keys ... not allowed`). Wrap one-time DDL so it only runs on create + `--full-refresh`:
```sqlx
post_operations {
  ${when(!incremental(), `ALTER TABLE ${self()} ADD PRIMARY KEY (order_date)`)}
}
```
(The PK also gives the incremental upsert its `ON CONFLICT` target.) Full-replace `table`s are recreated each run, so their constraint post-ops need no guard. Matview post-ops also re-run every build — keep them idempotent (matviews can't have PKs anyway).

### 10. Metadata for AI/BI engines + assertions (persists into the warehouse catalog)
Text-to-SQL agents and BI copilots answer questions from **warehouse catalog metadata** — sqlanvil is how that metadata gets there, re-applied on every run so rebuilds keep it. Document every model:
- `description:` (object comment) + `columns: { col: "..." }` (per-column comments) → `COMMENT ON TABLE|VIEW|MATERIALIZED VIEW|COLUMN`, readable back from `pg_description`/`information_schema` by any catalog-aware tool. (MySQL: tables/incrementals only — see the MySQL section. BigQuery-target projects: table + column descriptions, nested fields included.)
- **Name join targets in column descriptions** — `customer_id: "Foreign key to public.customers.customer_id."` gives an AI engine the relationship map even before real constraints.
- **PK/FK constraints go in `post_operations`** (there is no config field for them) — real enforced constraints on PG/MySQL; on a **BigQuery-target** project (≥1.24 tooling swap) append `NOT ENFORCED` (BigQuery stores them as catalog metadata):
```sqlx
post_operations {
  ALTER TABLE ${self()} ADD CONSTRAINT fk_orders_customer
    FOREIGN KEY (customer_id) REFERENCES ${ref("customers")} (customer_id)
}
```
On an incremental, wrap in `when(!incremental())` per delta #9.
- `assertions: { uniqueKey, uniqueKeys: [["a","b"],["c"]], nonNull: [...], rowConditions: [...] }` make key claims **tested** every run (`uniqueKey` and `uniqueKeys` are mutually exclusive — at most one). Standalone assertion = `type: "assertion"` whose `SELECT` returns offending rows (passes iff zero rows).
- The same metadata is queryable without a warehouse connection via the Parquet catalog artifacts (`sqlanvil query`, views `actions`/`columns`/`dependencies`) and `sqlanvil docs`. Full guide: https://sqlanvil.com/docs/guides/metadata/

### 11. No BigQuery-isms, ever
Don't emit `bigquery: {}`, `partitionBy`, `clusterBy`, `OPTIONS(...)`, `bigqueryPolicyTags`, backticked `` `project.dataset.table` ``, or `... NOT ENFORCED` constraints. Use the `postgres:` equivalents and standard double-quoted identifiers. (`NOT ENFORCED` is correct only on a **BigQuery-target** project — see delta #10.)

### 12. CLI: `sqlanvil <verb>` (the installed CLI — no global `dataform`, no `npm run`)
```bash
sqlanvil init      <projectDir> --warehouse postgres   # or supabase/mysql — scaffolds workflow_settings.yaml + .df-credentials.json template (BigQuery is the default and needs a GCP project + location). --bare skips the sample project (>=1.23); --interactive (>=1.23) is a guided human Q&A incl. converting a Dataform project — agents should use the flags
sqlanvil compile   <projectDir>
sqlanvil run       <projectDir> --credentials <projectDir>/.df-credentials.json
sqlanvil run       <projectDir> --credentials ... --full-refresh
sqlanvil run       <projectDir> --credentials ... --actions <name> --include-deps
sqlanvil validate  <projectDir> --credentials ...      # EXPLAIN-validate the whole DAG without executing (PG/Supabase/MySQL; BigQuery via dry-run)
sqlanvil test      <projectDir> --credentials ...
```
Install with `npm i -g @sqlanvil/cli`. (Working from a sqlanvil repo checkout instead of the installed CLI? Use `./scripts/run <verb>` in place of `sqlanvil <verb>`.)

**Migrating a whole Dataform project?** `sqlanvil migrate-dataform <srcDir> <outDir>` (≥1.22) converts it: the source dir is READ-ONLY, declarations become per-project runner-extract connections, targets get safe rewrites + inline `SQLANVIL-MIGRATE:` markers, and `migration-report.{md,json}` carries the to-do list (`validate` is the completion loop). Don't hand-translate file-by-file — run the converter, then work the report. **Staying on BigQuery?** `--target-warehouse bigquery` (≥1.24) is the same-warehouse tooling swap: SQL, `bigquery:{}` blocks, and declarations pass through untouched (no connections, no dialect pass); `defaultProject`/`defaultLocation` carry through and dataset casing is preserved.

**Generated projects ship agent guidance (≥1.24):** `init` (all modes) and `migrate-dataform` write a warehouse-tailored `AGENTS.md` + a `CLAUDE.md` bridge (`@AGENTS.md`) into the project — never overwriting existing ones. Read the project's own AGENTS.md first when working in a generated repo.

**`validate` / `run --dry-run` (≥ 1.9):** walks the DAG in dependency order, `EXPLAIN`-checks each model against the live warehouse in a throwaway shadow schema (empty stubs let downstream `${ref()}`s resolve), and reports **PASS / FAILURE / BLOCKED** (blocked = only an upstream failed) / SKIPPED (operations, imports). `run --dry-run` on Postgres/Supabase/MySQL now *validates* — it no longer silently executes. Any FAILURE/BLOCKED exits non-zero. Python script actions (≥1.20) get an env check instead of EXPLAIN — interpreter vs `pythonVersion`, requirements vs installed packages, syntax — without executing the script.

**`run --graph <file>` (≥ 1.17):** executes a stored `compile --json` output directly — no compile, no project source needed (bare dir + credentials works). What runs is exactly what was compiled (environment overrides are baked in — `--environment` with `--graph` is rejected; selection flags still apply). This is the release-artifact pattern: compile once, run the identical graph later/elsewhere.

**Queryable artifacts:** `compile`/`run` write Parquet artifacts under `<projectDir>/target/` (gitignore it). `sqlanvil query "<sql>"` runs SQL over them (views: `actions`, `dependencies`, `columns`, `runs`), `sqlanvil inspect` prints a project/run summary, `sqlanvil docs` renders a self-contained HTML catalog to `target/docs/index.html`. All warehouse-agnostic (no connection needed).

**Named environments (`--environment <name>`):** define dev/staging/prod in an `environments:` block in `workflow_settings.yaml`; each bundles non-secret overrides + a pointer to a gitignored credentials file:
```yaml
environments:
  dev:  { schemaSuffix: dev,  credentials: .df-credentials.dev.json }
  prod: { defaultDatabase: prod_db, vars: { region: us-prod }, credentials: .df-credentials.prod.json }
```
`sqlanvil run . --environment prod` loads prod's overrides + its credentials file (works on compile/run/test). Precedence: **explicit CLI flag > environment > workflow_settings defaults** (`vars` merge per-key). Each env's `credentials:` is a path to a **gitignored** `.df-credentials*.json` file — secrets never go in `workflow_settings.yaml`. `--schema-suffix` stays the low-level primitive.

### 13. Supabase extras (`warehouse: supabase`)
`supabase: {}` block adds `enableRls`, `publishToRealtime`, `ownerRole`, `vectors: [{ column, dimensions, indexType }]`. Dedicated action types: `rlsPolicy`, `realtimePublication`, `wrapper`, `vectorIndex`. `enableRls` only flips RLS on — declare actual policies via the `rlsPolicy` action.

### 14. Declaring external sources: `type: "declaration"`
Reference a pre-existing (externally-managed) table so `${ref()}` resolves and the DAG tracks it. Two equivalent forms:
```sqlx
-- one declaration per .sqlx file
config { type: "declaration", schema: "raw", name: "orders" }
```
```js
// many declarations in one .js file
declare({ schema: "raw", name: "orders" });
declare({ schema: "raw", name: "customers" });
```
**Declarations are exempt from `--schema-suffix` / `tablePrefix` / `datasetSuffix` — intentionally.** They point at fixed external tables, so the suffix is *not* applied to a declaration's own target, and `${ref()}` to a declared source resolves to the real (unsuffixed) table even under `--schema-suffix dev`. So a dev run reads true sources while writing to suffixed output schemas. Don't try to "fix" a declaration that lacks a suffix — that's correct.

### 15. Cross-warehouse sources: named connections + the auto-generated FDW bridge (≥ 1.2.0)

To read a table from **another warehouse** (BigQuery, or a second Postgres) from a Postgres/Supabase project, declare it as a **named connection** — do **not** hand-roll a foreign-data-wrapper. sqlanvil generates the whole FDW bridge for you. (Postgres/supabase sources need core **≥ 1.2.0** — that's when the `postgres_fdw` user mapping + run-time credential injection landed; BigQuery sources work from 1.1.1.)

**Step 1 — declare the connection** in `workflow_settings.yaml`:
```yaml
warehouse: supabase              # the ONE warehouse sqlanvil writes to
connections:
  bigquery_public:               # a BigQuery source
    platform: bigquery
    project: bigquery-public-data
    dataset: geo_us_boundaries
    billingProject: my-gcp-proj  # ≥1.13: bill YOUR project when `project` is read-but-not-bill (e.g. public data)
    saKeyId: "<vault-secret-id>" # NON-secret Vault pointer (fdw mode); the SA key lives in Supabase Vault, not here
    # mode: runner-extract       # ≥1.15: materialize at run time instead of a live FDW (see below)
  pg_source:                     # a Postgres source (non-secret coordinates only)
    platform: postgres
    host: db.example.com
    port: 5432
    database: analytics
    defaultSchema: public
  shop_mysql:                    # ≥1.18: a MySQL/MariaDB source — runner-extract ONLY (no PG FDW for MySQL)
    platform: mysql
    host: mysql.example.com
    port: 3306
    database: shop
```
For a **postgres/supabase source**, the secret `user`/`password` go in `.df-credentials.json` under `connections.<name>` (NOT in workflow_settings) — sqlanvil injects them into the generated `CREATE USER MAPPING` at run time. For a **mysql source**, `connections.<name>` holds `{ host, port, user, password, sslMode? }`.
The read (write) warehouse must be **`postgres` or `supabase`** — reading a connection from a `warehouse: bigquery`/`mysql` project errors. Connections are **read-only sources**: one R/W warehouse, everything else is a source you pull *from* (no write-back).

**Two source modes (`mode:` on the connection):**
- **`fdw`** (default for bigquery/postgres/supabase) — a live foreign table; needs the `wrappers` extension (+ Vault secret for BigQuery) on the warehouse.
- **`runner-extract`** (≥1.15 BigQuery; the default and ONLY mode for mysql, ≥1.18) — the CLI reads the source at run time and **materializes** the rows into a plain `<connection>_ext.<name>` table (capped 1M rows / 512MB). No Vault secret, no `wrappers`/`postgis` — works on bare/ephemeral databases where an FDW can't exist (branch CI). BigQuery auth in `connections.<name>` can be **keyless**: a short-lived `accessToken` (≥1.14), a `credentials` SA key, or ADC. An explicit `mode: fdw` on a mysql connection is a **compile error**.

**Step 2 — tag a declaration with the connection.** It **requires `columnTypes`** (the FDW needs them to build the foreign table), expressed in **Postgres** types (the foreign table is a Postgres object):
```sqlx
config {
  type: "declaration",
  connection: "bigquery_public",
  name: "zip_codes",
  columnTypes: { zip_code: "text", internal_point_lat: "float8", internal_point_lon: "float8" }
}
```
Missing `columnTypes`: a **compile error** in fdw mode; on runner-extract (≥1.22) it COMPILES and the extract **fails at run time** with the exact `introspect` command to fix it — either way, don't omit them.

**`schema:` on a connection-tagged declaration (≥1.22)** overrides the connection's dataset/database AND names the schema the extract/foreign table lands in (`schema: "ods", name: "zip_code"` → `ods.zip_code`; one connection per source project serves many datasets; schema-qualified `${ref("ods", "zip_code")}` works). No `schema:` = the legacy `<connection>_ext.<name>` landing spot.

**Step 3 — generate that declaration instead of hand-writing it** (preferred):
```bash
sqlanvil introspect <connection> <schema.table> --output definitions/sources/<name>.sqlx
# e.g. sqlanvil introspect bigquery_public geo_us_boundaries.zip_codes --output definitions/sources/zip_codes.sqlx
```
`introspect` reads the live source schema and writes the declaration with each source column mapped to a Postgres type — BigQuery→PG, Postgres→PG, and **MySQL→PG since 1.18** (`datetime`→`timestamp`, `double`→`double precision`, `json`→`jsonb`, blobs→`bytea`) — because `columnTypes` define the bridge/extract table *in the warehouse*. (`--output` writes the file; otherwise it prints to stdout.) It connects from your machine at build time, so it needs **read creds for the source** in `.df-credentials.json` under a `connections` map — `{ "connections": { "<name>": { ...source creds... } } }` (BigQuery: `{ "credentials": <SA key JSON> }` or a keyless `accessToken`; Postgres/MySQL: `host`/`port`/`database`/`user`/`password`/`sslMode`). This sits alongside the flat write-warehouse creds; `run` also reads the `connections` map for runner-extract sources and user-mapping injection.

**What `compile` auto-generates** (you never write these): in `fdw` mode, a foreign server **`<connection>_srv`** and a ref-able foreign table in schema **`<connection>_ext`** — e.g. `bigquery_public_ext.zip_codes`; in `runner-extract` mode, a ref-able **extract action** that materializes the same `<connection>_ext.<name>` table at run time. Downstream models just `${ref("zip_codes")}` either one like any other source; multiple declarations on one connection share the server. **Don't** hand-write a `wrapper`/foreign-table action to read a source — named connections replace that manual path.

### 16. File exports and imports (`type: "export"` ≥1.8, `type: "import"` ≥1.12)

Config-only actions that move **files** across the warehouse boundary (Parquet/CSV/JSON; local paths or `s3://`/`gs://` URIs — bucket creds go under a `storage:` map in `.df-credentials.json`):
```sqlx
config { type: "import", dataset: "oa_ext", name: "openaddresses_us",
         import: { location: "data/sample.csv", format: "csv", overwrite: true } }
```
- **`import`** loads a file into a warehouse table — a *producer*, `${ref()}`-able like any source (on Postgres/Supabase via the runner-side DuckDB bridge; BigQuery uses native `LOAD DATA`, gs:// only; MySQL throws). `location` is the verbatim source path/glob/URI — NOT a derived filename; `overwrite` defaults true (drop+create), `false` appends. No transform on the way in — shape it downstream.
- **`export`** writes a query's result to a file — a terminal *sink* (nothing can `ref()` it).
- `validate` marks imports SKIPPED (file columns unknown pre-run) and downstream models BLOCKED — expected, not a bug. Hosted SQLAnvil Cloud rejects *local* paths at compile (ephemeral runner disk) — use object-store URIs there.

### 17. Python script actions (`python:` in actions.yaml, ≥1.20)

Execution-time Python steps as DAG nodes — the staging/glue slot (download, unzip, API call) before an `import`. Declared in `definitions/actions.yaml` (NOT a new file extension; NOT compile-time like `.js` — compile never runs Python):
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
- Contract: cwd = project dir; env `SA_VARS` (vars as JSON) + `SA_ACTION_NAME`; `args:` → `sys.argv[1:]`; exit 0 = success; 30-min default timeout (`timeoutMillis`).
- **No warehouse credentials are injected** — the script stages FILES; a downstream `type: "import"` with `dependencyTargets: [{name: "fetch_data"}]` loads them. Never have the script write to the warehouse itself.
- The user owns the environment (pip/uv install is their job); `sqlanvil validate` verifies it — never installs. A failing script BLOCKs dependents.
- Hosted SQLAnvil Cloud rejects script actions at compile — local CLI / BYO CI only.
- `python:` is sugar for the language-neutral `script: { language: "python", ... }` (proto fields: `filename`, `depsFile`, `runtimeVersion`, `envRoot`).

## MySQL / MariaDB (`warehouse: mysql`)

One adapter serves **both MySQL 8 and MariaDB 11** — same `warehouse: mysql`, same generated SQL (MariaDB-specific features ride through `operations`). The MySQL surface is **deliberately smaller** than Postgres and several deltas above **invert** — read this before authoring a MySQL project.

**Config & credentials**
- `workflow_settings.yaml`: `warehouse: mysql`. `defaultDataset` = the MySQL **database** (MySQL has no schema-vs-database split — "schema" *is* the database). `defaultAssertionDataset` is a separate database. Pin the current core (`sqlanvilCoreVersion: 1.24.0`; MySQL warehouse needs ≥1.5, full `mysql:{}` block ≥1.19).
- `.df-credentials.json`: flat **`MysqlConnection`** — exact fields `host port database user password sslMode`. **No `defaultSchema`** (unlike Postgres). `sslMode`: `"disable"` (default/local) or `"require"`. Default port `3306`. Compiled identifiers are two-part backticks `` `db`.`table` `` (not BigQuery's single dotted-backtick, not Postgres double-quotes).

**The inversions — do NOT carry the Postgres rules over**
- **`mysql: {}` config block (indexes + table options + partitioning).** Declare secondary indexes (`indexes: [{ name?, columns, unique?, type? }]`) and table options (`engine`, `charset`, `collation`, `rowFormat` ≥1.19) in config — the role delta #3's `postgres: {}` plays. Index `type:` is `"fulltext"` or `"spatial"` (≥1.19; mutually exclusive with `unique`; a SPATIAL index needs a NOT NULL SRID geometry column, which CTAS doesn't produce — usually needs a `post_operations` `MODIFY` first). A column may carry a **prefix length in MySQL's own syntax** — `"body(50)"` → `` `body`(50) `` (required to index TEXT/BLOB, ≥1.19). No `WHERE`/`INCLUDE`/`opclass` (Postgres-only). **Native partitioning** via `mysql: { partition: { kind, expression, partitions: [{name, values}], count } }` (≥1.11; `kind`: RANGE=0, LIST=1, HASH=2, KEY=3; NB MySQL requires partition columns in every UNIQUE/PRIMARY key — a partitioned incremental's `uniqueKey` must include them). Use `mysql: {}`, never `postgres: {}`, on a mysql model — a `postgres:` block is the wrong dialect and silently ignored.
- **Incremental `uniqueKey` is sufficient — don't add your own unique index/PK.** `uniqueKey: ["id"]` compiles to `INSERT ... ON DUPLICATE KEY UPDATE`, and the adapter **auto-creates the matching unique index** (`uq_<db>_<table>`) on the first / `--full-refresh` build. Adding your own PK/unique for the merge (the Postgres `ON CONFLICT` pattern of delta #9) duplicates it.
- **Materialized views are emulated as a refreshed table snapshot.** `type: "view", materialized: true` builds a real table via drop + CTAS each run (refresh = re-run), honoring the `mysql: {}` block (engine/charset/rowFormat/indexes — the view config's `mysql:` block compiles through since **1.19**; earlier versions silently ignored it). No native matview, so it reads back as a table; no `refreshPolicy` / `noData` (those are Postgres-only).
- **`description:` / `columns:` apply as real DB comments.** They produce table/column comments via `ALTER TABLE … COMMENT` / `MODIFY COLUMN … COMMENT` and read back from `information_schema` (same documentation surface as Postgres). Tables/incrementals only — MySQL views can't carry comments, so a view's `description:`/`columns:` are skipped. Assertions (standalone + auto `assertions: {}`) also work.
- **A mysql WAREHOUSE can't read cross-warehouse sources — but MySQL as a SOURCE works (≥1.18).** Only a postgres/supabase warehouse reads `connections`; a `warehouse: mysql` project tagging a declaration with `connection:` errors. The other direction shipped in 1.18: a `platform: mysql` connection feeds a Postgres/Supabase warehouse via **runner-extract only** (no PG FDW for MySQL; `mode: fdw` = compile error) — see delta #15. `sqlanvil introspect` scaffolds declarations from a MySQL source, mapping MySQL→Postgres types.

**Same as Postgres/everywhere**
- Statement separator is `---`, never `;` (delta #6). **Do not use `DELIMITER`** — it's a mysql-client directive, not a server statement. A `CREATE PROCEDURE ... BEGIN ... ; ... END` is one statement between `---` separators; its internal `;` survive.
- `type: "operations"` for procedures/functions/triggers/grants (delta #7) — backtick-quote identifiers in raw DDL, never double-quote. `uniqueKey` (merge) vs `assertions.uniqueKey` (quality check) distinction (delta #8) holds.
- CLI: `sqlanvil init <dir> --warehouse mysql`. Boot local engines with `./tools/mysql/run-mysql-db.sh` (mysql:8 on 3306, mariadb:11 on 3307).

## Quick Reference: Dataform/BigQuery → sqlanvil/Postgres

| You'd reach for (Dataform/BQ) | Use instead (sqlanvil/PG) |
|---|---|
| `dataform.json` | `workflow_settings.yaml`, `warehouse: postgres` |
| `defaultProject` / `defaultLocation` | drop them; `defaultDataset` = schema |
| `bigquery: { partitionBy, clusterBy }` | `postgres: { partition: {...}, indexes: [...] }` |
| `OPTIONS(...)` / table options | `postgres: { fillfactor, unlogged, tablespace }` |
| `CREATE INDEX` in `post_operations` | `postgres: { indexes: [...] }` |
| `method: "btree"` (string) | `method: 0` (numeric enum) |
| `;` between statements | `---` |
| `CREATE PROCEDURE` + run separately | `type: "operations"` |
| creds `{postgres:{username,databaseName,ssl}}` | flat `{host,port,database,user,password,sslMode,defaultSchema}` |
| in-place matview refresh | `postgres: { refreshPolicy: "on_dependency_change" }` in the view config (else drop+recreate) |
| `dataform run` / `npm run` | `sqlanvil run ... --credentials` |
| hand-written FDW / foreign table to read another warehouse | named connection (`connections:` + `connection:`-tagged declaration), generated via `sqlanvil introspect` |
| `dataformCoreVersion:` | `sqlanvilCoreVersion:` (sqlanvil's own SemVer line) |
| BigQuery `LOAD DATA` / hand-rolled `COPY` to ingest a file | `type: "import"` (≥1.12; DuckDB bridge on PG/Supabase) |
| `EXPORT DATA` only-on-BQ | `type: "export"` (≥1.8; Parquet/CSV/JSON, local or s3://gs://) |
| hoping `--dry-run` validates | `sqlanvil validate` — EXPLAIN-checks the whole DAG (PG/Supabase/MySQL) |

## Common Mistakes (observed in real agent baselines)

1. Hand-rolling indexes/fillfactor in `post_operations` → use the `postgres: {}` block.
2. `method: "btree"` string → numeric enum (`0`).
3. Nesting credentials under `"postgres"` or using `username`/`databaseName`/`ssl` → flat `PostgresConnection`.
4. Leaving `bigquery: {}`, `defaultProject`, `clusterBy`, `bigqueryPolicyTags` in → remove all of it.
5. `;` separators in operations → `---` (and trust `$$...$$` bodies).
6. For a materialized view, set `postgres: { refreshPolicy, noData, indexes }` in the view config (the view's `postgres:` block IS supported).
7. Assuming a `dataform` CLI → it is `sqlanvil` (or `./scripts/run` in a repo checkout).
8. Confusing incremental `uniqueKey` (merge key) with `assertions.uniqueKey` (quality check).
9. `opclass: ["..."]` as an array → `opclass` is a single string applied to every indexed column.
10. Bare `ADD PRIMARY KEY`/`ADD CONSTRAINT` in an incremental's `post_operations` → wrap in `when(!incremental())`, or it re-runs on every append and errors.

## Red Flags — STOP and check the deltas

If you're about to type any of these in a Postgres/Supabase sqlanvil project, you're reverting to BigQuery priors:
- `dataform.json`, `defaultProject`, `defaultLocation`
- `bigquery: {`, `partitionBy`, `clusterBy`, `OPTIONS(`
- `method: "` (string) inside an index
- `CREATE INDEX` / `SET (fillfactor` inside `post_operations`
- `;` to separate statements in an operation
- `ADD PRIMARY KEY`/`ADD CONSTRAINT` in an incremental's `post_operations` without `when(!incremental())`
- a bare `dataform` or `npm run` command
- `dataformCoreVersion:` in `workflow_settings.yaml` (the field is `sqlanvilCoreVersion:`)
- a `connection:`-tagged declaration with **no** `columnTypes`, or hand-writing a foreign table instead of using a named connection
- reading a connection from a `warehouse: bigquery`/`mysql` project (the read side must be `postgres`/`supabase`)
- `mode: fdw` on a `platform: mysql` connection (MySQL sources are runner-extract only — compile error)
- `columnTypes` written in the SOURCE dialect (`datetime`, `double`) — they define the bridge/extract table IN THE WAREHOUSE, so they must be Postgres types (`timestamp`, `double precision`); use `introspect`, which maps them for you

On a **`warehouse: mysql`** project specifically:
- a `postgres: {}` (or `bigquery: {}`) block, or `defaultSchema` in the credentials, or double-quoted identifiers in raw DDL (MySQL uses backticks)
- expecting `materialized: true` to be a live/incremental matview — on MySQL it's a refreshed table snapshot (drop + CTAS each run), not a native materialized view
- a hand-added `PRIMARY KEY`/unique index just to make an incremental `uniqueKey` merge work (the adapter creates it for you)
- `DELIMITER $$` around a procedure body (sqlanvil splits on `---`, not `;` — `DELIMITER` is a client-only directive and will fail)
- expecting `description:`/`columns:` on a **view** to produce comments (MySQL views can't carry comments; on tables/incrementals they now do)
- raw `CREATE FULLTEXT/SPATIAL INDEX` or `PARTITION BY` DDL in `operations` — those are first-class in the `mysql: {}` block now (indexes `type:`, prefix `"col(50)"`, `rowFormat`, `partition:`); raw DDL remains only for exotica (AUTO_INCREMENT seeds etc.)
- `unique: true` combined with `type: "fulltext"|"spatial"` on an index (mutually exclusive — build error)

When unsure of a `postgres:` field name or enum value, read `protos/configs.proto`.

---

Full documentation: https://sqlanvil.com/docs · Engine source: https://github.com/SQLAnvil/sqlanvil · Hosted runs & CI: https://app.sqlanvil.com (remote MCP server at `/api/mcp`).
