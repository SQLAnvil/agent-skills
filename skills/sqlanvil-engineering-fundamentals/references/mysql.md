# MySQL / MariaDB (`warehouse: mysql`)

Read this before authoring a `warehouse: mysql` project. One adapter serves **both MySQL 8 and
MariaDB 11** — same `warehouse: mysql`, same generated SQL (MariaDB-specific features ride
through `operations`). The MySQL surface is **deliberately smaller** than Postgres and several of
the Postgres deltas in SKILL.md **invert** here.

## Config & credentials

- `workflow_settings.yaml`: `warehouse: mysql`. `defaultDataset` = the MySQL **database** (MySQL
  has no schema-vs-database split — "schema" *is* the database). `defaultAssertionDataset` is a
  separate database. Pin the current core (`sqlanvilCoreVersion: 1.36.0`; MySQL warehouse needs
  ≥1.5, full `mysql:{}` block ≥1.19).
- `.df-credentials.json`: flat **`MysqlConnection`** — exact fields `host port database user
  password sslMode`. **No `defaultSchema`** (unlike Postgres). `sslMode`: `"disable"`
  (default/local) or `"require"`. Default port `3306`. Compiled identifiers are two-part
  backticks `` `db`.`table` `` (not BigQuery's single dotted-backtick, not Postgres double-quotes).

## The inversions — do NOT carry the Postgres rules over

- **`mysql: {}` config block (indexes + table options + partitioning).** Declare secondary indexes
  (`indexes: [{ name?, columns, unique?, type? }]`) and table options (`engine`, `charset`,
  `collation`, `rowFormat` ≥1.19) in config — the role the `postgres: {}` block plays. Index
  `type:` is `"fulltext"` or `"spatial"` (≥1.19; mutually exclusive with `unique`; a SPATIAL
  index needs a NOT NULL SRID geometry column, which CTAS doesn't produce — usually needs a
  `post_operations` `MODIFY` first). A column may carry a **prefix length in MySQL's own syntax**
  — `"body(50)"` → `` `body`(50) `` (required to index TEXT/BLOB, ≥1.19). No
  `WHERE`/`INCLUDE`/`opclass` (Postgres-only). **Native partitioning** via
  `mysql: { partition: { kind, expression, partitions: [{name, values}], count } }` (≥1.11;
  `kind`: `"range"`, `"list"`, `"hash"` or `"key"`; NB MySQL requires partition columns in every
  UNIQUE/PRIMARY key — a partitioned incremental's `uniqueKey` must include them). Use `mysql: {}`,
  never `postgres: {}`, on a mysql model — a `postgres:` block is the wrong dialect and silently
  ignored.
- **Incremental `uniqueKey` is sufficient — don't add your own unique index/PK.** `uniqueKey:
  ["id"]` compiles to `INSERT ... ON DUPLICATE KEY UPDATE`, and the adapter **auto-creates the
  matching unique index** (`uq_<db>_<table>`) on the first / `--full-refresh` build. Adding your
  own PK/unique for the merge (the Postgres `ON CONFLICT` pattern of delta #9) duplicates it.
- **Materialized views are emulated as a refreshed table snapshot.** `type: "view", materialized:
  true` builds a real table via drop + CTAS each run (refresh = re-run), honoring the `mysql: {}`
  block (engine/charset/rowFormat/indexes; the view config's `mysql:` block compiles through
  since 1.19). No native matview, so it reads back as a table; no `refreshPolicy` / `noData`
  (those are Postgres-only). Tell the user this in the hand-over — they will otherwise expect a
  live `REFRESH`.
- **`description:` / `columns:` apply as real DB comments.** They produce table/column comments
  via `ALTER TABLE … COMMENT` / `MODIFY COLUMN … COMMENT` and read back from
  `information_schema` (same documentation surface as Postgres). Tables/incrementals only — MySQL
  views can't carry comments, so a view's `description:`/`columns:` are skipped. Assertions
  (standalone + auto `assertions: {}`) also work.
- **A mysql WAREHOUSE can't read cross-warehouse sources — but MySQL as a SOURCE works (≥1.18).**
  Only a postgres/supabase warehouse reads `connections`; a `warehouse: mysql` project tagging a
  declaration with `connection:` errors. The other direction: a `platform: mysql` connection feeds
  a Postgres/Supabase warehouse via **runner-extract only** (no PG FDW for MySQL; `mode: fdw` =
  compile error) — see `cross-warehouse-sources.md`. `sqlanvil introspect` scaffolds
  declarations from a MySQL source, mapping MySQL→Postgres types.

## Same as Postgres/everywhere

- Statement separator is `---`, never `;` (delta #6). **Do not use `DELIMITER`** — it's a
  mysql-client directive, not a server statement. A `CREATE PROCEDURE ... BEGIN ... ; ... END` is
  one statement between `---` separators; its internal `;` survive.
- `type: "operations"` for procedures/functions/triggers/grants (delta #7) — backtick-quote
  identifiers in raw DDL, never double-quote. `uniqueKey` (merge) vs `assertions.uniqueKey`
  (quality check) distinction (delta #8) holds.
- CLI: `sqlanvil init <dir> --warehouse mysql`. Any local `mysql:8` or `mariadb:11` container on
  3306 works for development.

## Red flags on a `warehouse: mysql` project

- a `postgres: {}` (or `bigquery: {}`) block, or `defaultSchema` in the credentials, or
  double-quoted identifiers in raw DDL (MySQL uses backticks)
- expecting `materialized: true` to be a live/incremental matview — on MySQL it's a refreshed
  table snapshot (drop + CTAS each run), not a native materialized view
- a hand-added `PRIMARY KEY`/unique index just to make an incremental `uniqueKey` merge work (the
  adapter creates it for you)
- `DELIMITER $$` around a procedure body (sqlanvil splits on `---`, not `;` — `DELIMITER` is a
  client-only directive and will fail)
- expecting `description:`/`columns:` on a **view** to produce comments (MySQL views can't carry
  comments; on tables/incrementals they do)
- raw `CREATE FULLTEXT/SPATIAL INDEX` or `PARTITION BY` DDL in `operations` — those are
  first-class in the `mysql: {}` block (indexes `type:`, prefix `"col(50)"`, `rowFormat`,
  `partition:`); raw DDL remains only for exotica (AUTO_INCREMENT seeds etc.)
- `unique: true` combined with `type: "fulltext"|"spatial"` on an index (mutually exclusive —
  build error)
