---
name: sqlanvil-engineering-fundamentals
description: Use whenever a task touches a sqlanvil data project (the Dataform fork for PostgreSQL, Supabase and MySQL/MariaDB) - writing or editing .sqlx models, workflow_settings.yaml, .df-credentials.json, definitions/*.js, actions.yaml, Supabase RLS policies, indexes, partitions, materialized views, incremental upserts, reading BigQuery or another database from a Postgres warehouse, or converting a Dataform project. Also use it when the user says "Dataform on Postgres/Supabase" or runs `sqlanvil compile|run|init|introspect|validate`. It corrects the Dataform/BigQuery habits that compile clean but misbehave (or fail) on these warehouses, and says what to tell the user in the hand-over.
license: Apache-2.0
metadata:
  source: https://github.com/SQLAnvil/sqlanvil
---

# sqlanvil Engineering Fundamentals

## Overview

**sqlanvil is a fork of Dataform repositioned for PostgreSQL, Supabase, and MySQL/MariaDB.** Your Dataform/BigQuery instincts are *mostly* right — `.sqlx` files, `config {}` blocks, `${ref()}`, `${self()}`, declarations, assertions, tags, incremental tables, `pre_operations`/`post_operations` all work the same. **This skill is the delta**: the places where assuming Dataform/BigQuery produces broken sqlanvil code, and the handful of things the compiler won't catch for you.

**Core principle:** when the warehouse is Postgres/Supabase, reach for the **`postgres: {}` config block** and idiomatic Postgres — never BigQuery options or hand-rolled DDL workarounds. MySQL/MariaDB has a deliberately smaller surface and several Postgres rules *invert* — read `references/mysql.md` before authoring a `warehouse: mysql` project.

**Carry-over fundamentals** apply unchanged: assertions first, a layered DAG (sources → intermediate → outputs), `${ref()}` for every table reference (never a hard-coded `schema.table`), and `description:` + `columns: {}` on every model.

**Source of truth for config fields:** [`protos/configs.proto`](https://github.com/SQLAnvil/sqlanvil/blob/main/protos/configs.proto) (`PostgresOptions`, `MysqlOptions`, `SupabaseOptions`, `PostgresConnection`, `MysqlConnection`, `ConnectionConfig`). When unsure of a field or enum value, read the proto — don't guess, and don't "fix" a compile error by adding a `bigquery:` block.

## Start here: let the scaffold and the compiler do the first pass

1. `sqlanvil init <dir> --warehouse postgres|supabase|mysql --bare` scaffolds `workflow_settings.yaml` with the current pin, a `.df-credentials.json` template in the right shape, a `.gitignore` that already ignores the credentials, and a warehouse-tailored `AGENTS.md`. Start from it; read that AGENTS.md.
2. `sqlanvil compile <dir>` needs no warehouse. Compile after every file. Its errors are specific (`Unrecognized action type`, `Unexpected property`), so a wrong guess costs one round trip — but only for things the engine *rejects*. The deltas below are mostly the things it **accepts and gets wrong**, or that you only find out about at the second run.
3. `sqlanvil compile <dir> --json` shows what will actually run: `postgres`/`mysql`/`supabase` blocks as parsed, `postOps` vs `incrementalPostOps`, operations split into statements. Check it when a delta says "silently".

Everything else — commands and flags, environments, migration, cross-warehouse sources, file imports and Python steps, MySQL, older pins — is in `references/` (see "Beyond the basics").

## The deltas that bite

### 1. Project config: `workflow_settings.yaml`
```yaml
warehouse: postgres            # flat string: "postgres", "supabase" or "mysql" — NOT nested
defaultDataset: public         # the Postgres SCHEMA (on MySQL: the database)
defaultAssertionDataset: sqlanvil_assertions
sqlanvilCoreVersion: 1.36.0    # sqlanvil's OWN SemVer line (NOT dataformCoreVersion); pin the current release
vars:
  someVar: value
```
**Drop** `defaultProject` and `defaultLocation` — they are BigQuery-only. `dataform.json` is the legacy upstream name; sqlanvil reads `workflow_settings.yaml`.

### 2. Connection: `.df-credentials.json` is a flat `PostgresConnection`
The filename is literally `.df-credentials.json` (the fork kept upstream's name; override with `--credentials`). The JSON is **flat** — **not** nested under a `"postgres"` key, **not** the BigQuery shape, and the field names are exactly `host port database user password sslMode defaultSchema` (**not** `username`/`databaseName`/`ssl`):
```json
{ "host": "localhost", "port": 5432, "database": "postgres", "user": "postgres",
  "password": "password", "sslMode": "require", "defaultSchema": "public" }
```
Supabase: the pooler host (`aws-…pooler.supabase.com`, user `postgres.<ref>`) or `db.<ref>.supabase.co`, port 5432 (6543 = transaction pooler), `sslMode: "require"`. MySQL: same fields minus `defaultSchema`, `sslMode: "disable"` for a plain local/internal server. The file is gitignored by the scaffold — keep it that way, and say so in the hand-over.

### 3. Storage, indexes, partitioning are FIRST-CLASS — never hand-roll DDL
Use the `postgres: {}` block on `type: "table"`, `"incremental"` and `"view"`. Do **not** create indexes or set fillfactor via `post_operations` raw DDL: the config form is re-applied correctly on `--full-refresh`, survives `--schema-suffix`, and shows up in the catalog.
```sqlx
config {
  type: "table",
  postgres: {
    fillfactor: 80, unlogged: false, tablespace: "fast_ssd",
    indexes: [
      { name: "idx_email", columns: ["email"], unique: true },
      { name: "idx_props", columns: ["props"], method: "gin", opclass: "jsonb_path_ops" }
    ]
  }
}
```
Index fields: `name`, `columns[]`, `method` (`"btree"` default — omit it — `"hash"`, `"gin"`, `"gist"`, `"brin"`; an unknown name is a compile error), `where` (partial predicate), `unique`, `include[]` (covering), `opclass` (**a single string** applied to every indexed column — `opclass: "gin_trgm_ops"`, **not** an array).

**Known engine gap (1.36.0):** `fillfactor` is **silently dropped on a partitioned table** — the partitioned build path never emits `with (fillfactor=…)`, and Postgres would reject it on the parent anyway. If the user asks for both, say so: keep `fillfactor:` as declared intent and, if it matters, apply it to the leaf partitions in a guarded `post_operations` block.

### 4. Native partitioning via `postgres.partition` (not `partitionBy`/`clusterBy`)
```sqlx
postgres: {
  partition: {
    kind: "range",                             // "range" | "list" | "hash"
    columns: ["order_date"],
    partitions: [
      { name: "y2024", values: "FROM ('2024-01-01') TO ('2025-01-01')" }
    ],
    includeDefault: true
  }
}
```
`values` is the raw `FOR VALUES` body matching `kind`. There is no `clusterBy` — use `indexes` instead. A primary key on a partitioned table must include the partition column.

### 5. Materialized views: `type: "view", materialized: true`
Emits `CREATE MATERIALIZED VIEW`. Default = **drop + recreate every run** (which also picks up definition changes). The view's own `postgres: {}` block is supported:
```sqlx
config {
  type: "view", materialized: true,
  postgres: {
    refreshPolicy: "on_dependency_change",   // in-place REFRESH instead of drop+recreate
    noData: true,                            // CREATE ... WITH NO DATA (empty until first refresh)
    indexes: [{ name: "idx_mv_id", columns: ["id"], unique: true }]
  }
}
```
`refreshPolicy: "on_dependency_change"` runs `REFRESH MATERIALIZED VIEW` in place — but in-place refresh does **not** pick up definition (SQL) changes; omit it for the safe drop+recreate default. On MySQL there is no native matview: it's a rebuilt table snapshot (`references/mysql.md`).

### 6. Statement separator is `---`, never `;`
Three dashes on their own line separate statements in `operations`, `pre_operations`, `post_operations`. sqlanvil never splits on `;`, so PL/pgSQL `$$ ... ; ... $$` bodies and MySQL `BEGIN ... ; ... END` bodies survive intact (and `DELIMITER` is a mysql-client directive — never write it). A `table`/`view` model body is exactly ONE `SELECT` — no `---`.

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
SELECT marts.recalc()
```
`dependencies: ["model"]` orders it after the models it touches (the long form is `dependencyTargets: [{ dataset: "marts", name: "events" }]` — the key is `dataset`, not `schema`, and a wrong key is a compile error). Postgres calls a *function* with `SELECT`; `CALL` is for procedures.

### 8. Two different `uniqueKey`s — do not confuse them
- Top-level `uniqueKey: ["id"]` on an **incremental** = the upsert/merge key (Postgres `INSERT ... ON CONFLICT`, MySQL `ON DUPLICATE KEY UPDATE`). Controls incremental write behavior.
- `assertions: { uniqueKey: ["id"] }` = a uniqueness **assertion** action. A data-quality check.

They are independent and usually coexist. `incrementalStrategy` is BigQuery-only and a **compile error** on postgres/supabase/mysql — use the default merge with a `uniqueKey`, or delete the range you are replacing in `pre_operations`. `.jitCode()` / `jitData()` are compile errors too: generate SQL at compile time (`.query()`, or an operations action).

### 9. One-time DDL on incrementals → wrap in `when(!incremental())`
An **unwrapped** `pre_operations`/`post_operations` block runs on **every** run of an incremental — the initial create *and* every append (it is compiled into both `postOps` and `incrementalPostOps`). A bare `ALTER TABLE ... ADD PRIMARY KEY` (or `ADD CONSTRAINT ... FOREIGN KEY`) therefore errors on the second run (`multiple primary keys ... not allowed`). Wrap it so it only runs on create + `--full-refresh`:
```sqlx
post_operations {
  ${when(!incremental(), `ALTER TABLE ${self()} ADD PRIMARY KEY (order_date)`)}
}
```
The PK also gives the incremental upsert its `ON CONFLICT` target. Full-replace `table`s are recreated each run, so their constraint post-ops need no guard. Matview post-ops re-run every build — keep them idempotent. (MySQL: don't add the key at all — the adapter creates the unique index for `uniqueKey` itself.)

### 10. Metadata for AI/BI engines + assertions (persists into the warehouse catalog)
Text-to-SQL agents and BI copilots answer from **warehouse catalog metadata**; sqlanvil is how it gets there, re-applied on every run. Document every model:
- `description:` + `columns: { col: "..." }` → `COMMENT ON TABLE|VIEW|MATERIALIZED VIEW|COLUMN`, readable back from `pg_description`/`information_schema`. (MySQL: tables/incrementals only.)
- **Name join targets in column descriptions** — `customer_id: "Foreign key to public.customers.customer_id."` gives an AI engine the relationship map even before real constraints.
- **PK/FK constraints go in `post_operations`** (there is no config field for them); on an incremental, wrap in `when(!incremental())` per delta #9.
- `assertions: { uniqueKey, uniqueKeys: [["a","b"],["c"]], nonNull: [...], rowConditions: [...] }` make key claims **tested** every run (`uniqueKey` and `uniqueKeys` are mutually exclusive). `nonNull` compiles into the `..._assertions_rowConditions` action. Standalone assertion = `type: "assertion"` whose `SELECT` returns offending rows (passes iff zero rows).

### 11. No BigQuery-isms, ever
Don't emit `bigquery: {}`, `partitionBy`, `clusterBy`, `OPTIONS(...)`, `bigqueryPolicyTags`, backticked `` `project.dataset.table` ``, or `... NOT ENFORCED` constraints on a Postgres/Supabase/MySQL project. Use the `postgres:`/`mysql:` equivalents and the warehouse's own identifier quoting (double quotes on Postgres, backticks on MySQL).

### 12. Declaring external sources: `type: "declaration"`
Reference a pre-existing (externally-managed) table so `${ref()}` resolves and the DAG tracks it. One per `.sqlx` — `config { type: "declaration", schema: "raw", name: "orders" }` — or many in a `.js`: `declare({ schema: "raw", name: "orders" });`. Give declarations `description`/`columns` too; they document the source.

**Declarations are exempt from `--schema-suffix` / `tablePrefix` / `datasetSuffix` — intentionally.** They point at fixed external tables, so `${ref()}` to a declared source resolves to the real (unsuffixed) table even under `--schema-suffix dev`: a dev run reads true sources while writing to suffixed output schemas. Don't "fix" a declaration that lacks a suffix. A source in **another warehouse** is a declaration tagged with a named `connection:` — `references/cross-warehouse-sources.md`.

### 13. Supabase extras (`warehouse: supabase`)
**The Supabase actions are JavaScript calls (or `actions.yaml` entries), NOT `.sqlx` types** — `config { type: "rlsPolicy" }` is a compile error ("Unrecognized action type"). In a `definitions/*.js` file:
```js
rlsPolicy({ table: "orders_enriched", name: "orders_select_own", command: "select",
            roles: ["authenticated"], using: "auth.uid() = user_id" });   // withCheck: only for insert/update
realtimePublication({ table: "orders_enriched" });
vectorIndex({ table: "docs", name: "docs_embedding_idx", column: "embedding", indexType: "hnsw" });
```
`name` + `table` are required (`column` too for `vectorIndex`); Postgres rejects `withCheck` on `select`/`delete` policies. The generated policy action enables RLS on the table and depends on it automatically. `wrapper({...})` is JS-only.

The table-level **`supabase: {}` block** (on `table`/`incremental`, `warehouse: supabase` only — a compile error elsewhere) sets `enableRls`, `publishToRealtime`, `ownerRole`, `vectors: [{ column, indexType: "hnsw"|"ivfflat", params }]` and a nested `postgres: {}`. Applied when the table is created, after its indexes, before `post_operations`. `enableRls` only flips RLS on — declare policies with `rlsPolicy`. Remember: with RLS on and only a `select` policy, no non-service role can write the table — usually what a pipeline-owned table wants; say it in the hand-over.

### 14. CLI: `sqlanvil <verb>` (no `dataform`, no `npm run`)
```bash
sqlanvil init     <dir> --warehouse postgres|supabase|mysql [--bare]
sqlanvil compile  <dir> [--json]
sqlanvil run      <dir> --credentials <dir>/.df-credentials.json [--full-refresh] [--actions <name> --include-deps]
sqlanvil validate <dir> --credentials ...     # EXPLAIN-validates the whole DAG without executing
sqlanvil test     <dir> --credentials ...
```
`validate`, `run --graph`, Parquet artifacts (`sqlanvil query`/`inspect`/`docs`) and named environments (`--environment prod`): `references/cli-and-environments.md`.

## Beyond the basics (read the reference before writing)

| when the task involves | read |
|---|---|
| a `warehouse: mysql` / MariaDB project | `references/mysql.md` — the inversions: `mysql: {}` block, no PK for the merge, matview = snapshot, backticks, no `DELIMITER` |
| reading BigQuery, another Postgres, or MySQL from a Postgres/Supabase warehouse | `references/cross-warehouse-sources.md` — `connections:` + a `connection:`-tagged declaration with Postgres `columnTypes`; `fdw` vs `runner-extract`; the credentials shape (`credentials` = key JSON *content*, not a path); `sqlanvil introspect` |
| converting an existing Dataform project | `references/migrating-from-dataform.md` — `migrate-dataform` → `introspect_all.sh` → `migrate-fix`; the report is the to-do list; what the converter deliberately leaves |
| loading or exporting files, Python staging steps | `references/files-and-scripts.md` — `type: "import"` / `"export"`, `python:` actions in actions.yaml |
| commands, flags, `validate`, `run --graph`, environments, artifacts | `references/cli-and-environments.md` |
| a project pinned below the current release | `references/older-pins.md` — what each pin silently did differently (index names → btree before 1.33, etc.) |

## Hand-over: what to tell the user when you're done

Compiling is not running. The hand-over is where the baseline agent falls short, so make it explicit:

1. **How to run it**: `sqlanvil run <dir> --credentials <dir>/.df-credentials.json` (and `sqlanvil validate …` first when a warehouse is reachable). Name the `--environment` if you created one.
2. **What compile verified and what it didn't**: the graph, config blocks and dependency order are checked; DDL against the live warehouse, the upsert, and anything in a `$$` body are not.
3. **Credentials**: the password is in the gitignored `.df-credentials.json`; where the source credentials go for cross-warehouse connections (and that `credentials` wants the key's JSON content).
4. **Warehouse caveats that change expectations**: MySQL matview = rebuilt snapshot; runner-extract = materialized at run time (1M rows / 512MB cap), regenerate the declaration with `sqlanvil introspect <connection> <schema.table>`; fillfactor ignored on a partitioned table; in-place matview refresh won't pick up SQL changes.
5. **Assumptions you made** (time zone of partition bounds, `sslMode`, join type, extra indexes you added) so they can be reversed.

## Quick Reference: Dataform/BigQuery → sqlanvil/Postgres

| You'd reach for (Dataform/BQ) | Use instead (sqlanvil/PG) |
|---|---|
| `dataform.json` | `workflow_settings.yaml`, `warehouse: postgres` |
| `defaultProject` / `defaultLocation` | drop them; `defaultDataset` = schema |
| `dataformCoreVersion:` | `sqlanvilCoreVersion:` (sqlanvil's own SemVer line) |
| creds `{postgres:{username,databaseName,ssl}}` | flat `{host,port,database,user,password,sslMode,defaultSchema}` |
| `bigquery: { partitionBy, clusterBy }` | `postgres: { partition: {...}, indexes: [...] }` |
| `OPTIONS(...)` / table options | `postgres: { fillfactor, unlogged, tablespace }` |
| `CREATE INDEX` in `post_operations` | `postgres: { indexes: [...] }` (MySQL: `mysql: { indexes }`) |
| `;` between statements | `---` |
| `CREATE PROCEDURE` + run separately | `type: "operations"` |
| in-place matview refresh | `postgres: { refreshPolicy: "on_dependency_change" }` in the view config (else drop+recreate) |
| `config { type: "rlsPolicy" }` | `rlsPolicy({...})` in `definitions/*.js` or an `actions.yaml` entry |
| hand-written FDW / foreign table to read another warehouse | named connection + `connection:`-tagged declaration, generated by `sqlanvil introspect` |
| BigQuery `LOAD DATA` / hand-rolled `COPY` | `type: "import"` |
| `EXPORT DATA` | `type: "export"` |
| hoping `--dry-run` validates | `sqlanvil validate` |
| `dataform run` / `npm run` | `sqlanvil run ... --credentials` |

## Red Flags — STOP and check the deltas

If you're about to type any of these in a sqlanvil project, you're reverting to BigQuery (or plain-Dataform) priors:
- `dataform.json`, `defaultProject`, `defaultLocation`, `dataformCoreVersion:`
- `bigquery: {`, `partitionBy`, `clusterBy`, `OPTIONS(`, `incrementalStrategy` on a non-BigQuery warehouse
- `type: "rlsPolicy"` / `"vectorIndex"` / `"realtimePublication"` in a `.sqlx` config
- `CREATE INDEX` / `SET (fillfactor` / `PARTITION BY` inside `post_operations` or an operations action
- `;` to separate statements in an operation; `DELIMITER` anywhere
- `ADD PRIMARY KEY`/`ADD CONSTRAINT` in an incremental's `post_operations` without `when(!incremental())`
- `opclass: ["..."]` as an array (it is one string)
- a hard-coded `raw.orders` in model SQL instead of a declaration + `${ref()}`; "fixing" a declaration that has no schema suffix
- a bare `dataform` or `npm run` command; a hand-over that doesn't say how to run the project
- a `connection:`-tagged declaration with no `columnTypes`, `columnTypes` in the source dialect (`STRING`, `FLOAT64`), a hand-written foreign table, or a key file *path* where `credentials` wants the key's JSON content
- on `warehouse: mysql`: a `postgres: {}` block, `defaultSchema` in the credentials, double-quoted identifiers, a hand-added unique key for the merge, or promising a live materialized view

---

Full documentation: https://sqlanvil.com/docs · Engine source: https://github.com/SQLAnvil/sqlanvil · Hosted runs & CI: https://app.sqlanvil.com (remote MCP server at `/api/mcp`).
