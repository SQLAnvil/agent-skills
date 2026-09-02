---
name: sqlanvil-sqlx-lint
description: Use when creating or modifying sqlanvil .sqlx files, before committing them, or when a "sqlanvil-sqlx-lint" pre-commit hook fails - checks config-block and project conventions (columns documentation, ref() usage, schema suffixes, directory policies) plus the Dataform habits sqlanvil silently ignores or fails on at run time (bigquery options on Postgres, `;` instead of `---`, unguarded DDL on incrementals) that SQL linters and sqlanvil compile cannot see
---

# sqlanvil-sqlx-lint

## Overview

Deterministic convention checker for sqlanvil `.sqlx` files. SQL linters cover
the SQL body, `sqlanvil compile` covers syntax, and `sqlanvil validate` needs a
warehouse. This tool covers the `config {}` block, project-structure
conventions, and the sqlanvil-specific deltas, in milliseconds, offline. Run it
on every `.sqlx` file you create or modify, before `sqlanvil compile`.

## Usage

```bash
pip install sqlanvil-sqlx-lint     # once, into the project venv
sqlanvil-sqlx-lint definitions/path/to/file.sqlx [...]
```

Run from the project root: the target warehouse is read from
`workflow_settings.yaml` there, and `${ref()}` targets resolve for the E010
rule via `--definitions-root` (default `./definitions`). Exit 0 = clean or
warnings only; exit 1 = errors; exit 2 = bad config.

Configuration lives in `.sqlx-lint.toml` at the project root (or
`[tool.sqlx-lint]` in pyproject.toml) — read it first if present: it declares
the warehouse override, directory policies, opt-in rules, allowed schemas, and
E010 scope.

## Rules

| Code | Default | Meaning | Fix |
|------|---------|---------|-----|
| E001 | on | config block missing/unbalanced | add `config { ... }` |
| E002 | on | `columns: {}` missing/empty | document the columns (declarations: `columnTypes` from `sqlanvil introspect` also counts) |
| E003 | on | `schema:` hardcodes an env suffix | use the base name; `--schema-suffix` / `environments.<name>.schemaSuffix` appends it |
| E004 | on | `name:` matches filename | delete the `name:` line |
| E005 | opt-in | `schema:` on operations/assertions | delete it (`hasOutput: true` ops exempt) |
| E006 | on | hardcoded `schema.table` / `"s"."t"` / `` `db`.`t` `` / `` `p.d.t` `` | declare a source, use `${ref()}` |
| E007 | on* | directory naming/type policy violation | rename / fix type (*needs configured policies) |
| W008 | opt-in | `post_operations` before SELECT | move below the query |
| E010 | on | `columns:{}` misses determinable output columns | document every listed column |
| S101 | on | `bigquery:{}` / `partitionBy` / `clusterBy` on Postgres/MySQL | use `postgres: { indexes, partition }` |
| S102 | on | `;` between statements in operations blocks | separate with a `---` line |
| S103 | on | `postgres.indexes[].method: "btree"` | numeric enum (`BTREE=0, HASH=1, GIN=2, GIST=3, BRIN=4`) or omit |
| S104 | on | `incrementalStrategy` off BigQuery | default merge with `uniqueKey`, or delete the range in `pre_operations` |
| S105 | on | unguarded `ADD PRIMARY KEY` / `ADD CONSTRAINT` on an incremental | wrap in `${when(!incremental(), \`...\`)}` |
| S106 | on | both `uniqueKey` and `uniqueKeys` in `assertions:` | keep one |
| S108 | on | `.jitCode()` / `jitData()` | generate the SQL at compile time |

E006 matters most: hardcoded paths silently break the dependency graph. S101,
S102, and S105 are the ones nothing else catches: the project compiles and then
does the wrong thing at run time. E002/E010 matter for metadata consumers:
`columns:{}` becomes `COMMENT ON COLUMN` in the warehouse catalog, which is what
BI tools and AI analytics agents read.

## Suppressing

Only with genuine cause (e.g. a foreign-data-wrapper schema E006 can't know
about, or a documented temporary migration state):

```sql
from public.legacy_events -- sqlx-lint: disable=E006 (reason here)
```

or `-- sqlx-lint: disable-file=E006` anywhere in the file. Prefer the
`allowed_schemas` config key over per-line suppression for a schema that is
legitimately referenced directly everywhere.

## Common Mistakes

- Fixing the lint by suppressing instead of following the convention.
- Skipping the run because the SQL linter or `sqlanvil compile` passed — they
  check disjoint things.
- Editing a legacy file and suppressing E002: touching a file is the natural
  moment to add its column documentation.
- "Fixing" S101 by deleting the `bigquery: {}` block without carrying its intent
  (partitioning, clustering) into `postgres: { partition, indexes }`.

Source, tests, and config reference:
https://github.com/SQLAnvil/sqlanvil-sqlx-lint
