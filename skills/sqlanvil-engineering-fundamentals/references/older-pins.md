# Older pins: behaviour that depends on `sqlanvilCoreVersion`

SKILL.md describes the current release (the pin it shows). Read this only when a project pins
an older `sqlanvilCoreVersion` and you cannot bump it. The dangerous entries are the ones that
**silently** did something else — those are marked.

| feature | since | before that |
|---|---|---|
| Postgres index `method:` as a name (`"gin"`, `"gist"`, …) | 1.33 | a name **silently built a btree**; write the number: `BTREE=0, HASH=1, GIN=2, GIST=3, BRIN=4` |
| Postgres `partition.kind` as a name (`"range"`, `"list"`, `"hash"`) | 1.33 | numbers `0/1/2` |
| MySQL `partition.kind` as a name (`"range"`, `"list"`, `"hash"`, `"key"`) | 1.33 | names **silently became RANGE**; use `0`–`3` |
| table-level `supabase: {}` block | 1.33 | **silently ignored** — use the `rlsPolicy()` / `vectorIndex()` / `realtimePublication()` actions instead |
| Supabase actions as `actions.yaml` entries (`- rlsPolicy: {...}`) | 1.33 | JS calls in `definitions/*.js` only (`wrapper` is JS-only on every version) |
| required-field errors for Supabase actions (`name`, `table`, `column`) | 1.33 | missing fields were not caught at compile |
| MySQL `mysql:{}` block on a view config, `rowFormat`, index `type:` fulltext/spatial, prefix columns `"col(50)"` | 1.19 | the view's `mysql:` block was **silently ignored** |
| `.jitCode()` / `jitData()` rejected at compile | 1.30 | the action compiled clean and **silently did nothing** at run time |
| `incrementalStrategy` (BigQuery-only) | 1.29 | not available; it is a compile error on postgres/supabase/mysql on every version |
| `preserveGovernanceControls` accepted on `type: "incremental"` (BigQuery) | 1.32.7 | tables only |
| BigQuery `impersonateServiceAccount` in `connections.<name>` | 1.36 | SA key (`credentials`), `accessToken` (≥1.14) or ADC |
| MySQL/MariaDB as a *source* (`platform: mysql`, runner-extract only) + MySQL→PG `introspect` | 1.18 | not available |
| `mode: runner-extract` for BigQuery sources | 1.15 | fdw only (needs `wrappers` + Vault) |
| `billingProject:` on a BigQuery connection | 1.13 | billed to `project` |
| `schema:` on a connection-tagged declaration | 1.22 | always `<connection>_ext.<name>` |
| missing `columnTypes` on a runner-extract declaration = run-time error with the `introspect` hint | 1.22 | compile error |
| declarations inert until referenced | 1.27 | every runner-extract declaration produced an extract |
| Postgres/supabase sources via `postgres_fdw` + user-mapping injection | 1.2.0 | BigQuery sources only (from 1.1.1) |
| `type: "import"` / `type: "export"` | 1.12 / 1.8 | not available |
| `python:` script actions | 1.20 | not available |
| `sqlanvil validate` / `run --dry-run` validates on PG/Supabase/MySQL | 1.9 | `--dry-run` silently executed |
| `run --graph <file>` | 1.17 | not available |
| `migrate-dataform` / `--target-warehouse bigquery` / ADC-mode creds + `environments.test` / three-phase `migrate-fix` | 1.22 / 1.24 / 1.26 / 1.28 | see `migrating-from-dataform.md` |
| `init --bare`, `init --interactive`, generated AGENTS.md | 1.23 / 1.24 | sample project always scaffolded |
| MySQL warehouse | 1.5 (full `mysql:{}` block 1.19) | not available |
