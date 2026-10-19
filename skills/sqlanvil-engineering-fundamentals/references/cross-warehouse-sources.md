# Cross-warehouse sources: named connections + the generated FDW / extract bridge (≥1.2.0)

Read this when a Postgres/Supabase project needs to read a table that lives in **another
warehouse** (BigQuery, a second Postgres, or MySQL/MariaDB). Declare it as a **named
connection** — do **not** hand-roll a foreign-data-wrapper. sqlanvil generates the whole bridge.
(Postgres/supabase sources need core ≥1.2.0 — that's when the `postgres_fdw` user mapping +
run-time credential injection landed; BigQuery sources work from 1.1.1.)

## Step 1 — declare the connection in `workflow_settings.yaml`

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

Nothing secret goes in `workflow_settings.yaml`. For a **postgres/supabase source**, the secret
`user`/`password` go in `.df-credentials.json` under `connections.<name>` — sqlanvil injects them
into the generated `CREATE USER MAPPING` at run time. For a **mysql source**, `connections.<name>`
holds `{ host, port, user, password, sslMode? }`. For a **BigQuery source** see the credentials
shape below.

The read (write) warehouse must be **`postgres` or `supabase`** — reading a connection from a
`warehouse: bigquery`/`mysql` project errors. Connections are **read-only sources**: one R/W
warehouse, everything else is a source you pull *from* (no write-back).

## Two source modes (`mode:` on the connection)

- **`fdw`** (default for bigquery/postgres/supabase) — a live foreign table; needs the `wrappers`
  extension (+ Vault secret for BigQuery) on the warehouse.
- **`runner-extract`** (≥1.15 BigQuery; the default and ONLY mode for mysql, ≥1.18) — the CLI
  reads the source at run time and **materializes** the rows into a plain
  `<connection>_ext.<name>` table (capped 1M rows / 512MB). No Vault secret, no
  `wrappers`/`postgis` — works on bare/ephemeral databases where an FDW can't exist (branch CI,
  Supabase preview branches, any database where you can't install extensions). An explicit
  `mode: fdw` on a mysql connection is a **compile error**.

## BigQuery source credentials in `.df-credentials.json`

```json
{
  "host": "...", "port": 5432, "database": "postgres", "user": "...", "password": "...",
  "sslMode": "require", "defaultSchema": "public",
  "connections": {
    "bigquery_public": {
      "projectId": "my-gcp-proj",
      "credentials": "{\"type\":\"service_account\",\"project_id\":\"...\", ... }"
    }
  }
}
```

The warehouse creds stay flat at the top level; the `connections` map sits beside them. For
BigQuery, auth is one of:

- **`credentials`** — the service-account key's **JSON content, as a string** (the adapter
  `JSON.parse`s it). It is **not a file path**: `"credentials": "~/keys/sa.json"` fails at run
  time. Inline a key file with
  `jq --arg k "$(cat ~/keys/sa.json)" '.connections.bigquery_public.credentials = $k' .df-credentials.json`.
- **`accessToken`** (≥1.14) — a short-lived token, keyless.
- **`impersonateServiceAccount`** (≥1.36) — the CLI impersonates it over ADC or the key; the
  source identity needs `roles/iam.serviceAccountTokenCreator`.
- nothing — falls back to **ADC** (`gcloud auth application-default login`, or
  `GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json` in the environment).

`introspect` and `run` both read this map (`run` for runner-extract sources and user-mapping
injection).

## Step 2 — tag a declaration with the connection

It **requires `columnTypes`** (the bridge needs them to build the foreign/extract table),
expressed in **Postgres** types (the table is a Postgres object):

```sqlx
config {
  type: "declaration",
  connection: "bigquery_public",
  name: "zip_codes",
  columnTypes: { zip_code: "text", internal_point_lat: "float8", internal_point_lon: "float8" }
}
```

Missing `columnTypes`: a **compile error** in fdw mode; on runner-extract (≥1.22) it COMPILES and
the extract **fails at run time** with the exact `introspect` command to fix it — either way, don't
omit them on declarations something reads. Write `text` / `double precision` / `timestamptz`,
never the source's `STRING` / `FLOAT64` / `datetime` / `double`.

**Declarations are inert until referenced (≥1.27, Dataform parity).** A runner-extract declaration
only produces an extract action when some model `ref()`s it — unreferenced declarations (even
ones whose source table doesn't exist yet) compile clean and never run, so pre-declaring a source
system's catalog is safe and free. Don't "fix" unreferenced placeholder declarations, and don't
expect an unreferenced source to be materialized by a full run — add a `ref()` and it enters the
graph on the next compile. A *referenced* declaration with a missing source fails at run time,
like any SQL reading a missing table.

**`schema:` on a connection-tagged declaration (≥1.22)** overrides the connection's
dataset/database AND names the schema the extract/foreign table lands in (`schema: "ods", name:
"zip_code"` → `ods.zip_code`; one connection per source project serves many datasets;
schema-qualified `${ref("ods", "zip_code")}` works). No `schema:` = the legacy
`<connection>_ext.<name>` landing spot.

## Step 3 — generate that declaration instead of hand-writing it (preferred)

```bash
sqlanvil introspect <connection> <schema.table> --output definitions/sources/<name>.sqlx
# e.g. sqlanvil introspect bigquery_public geo_us_boundaries.zip_codes --output definitions/sources/zip_codes.sqlx
```

`introspect` reads the live source schema and writes the declaration with each source column
mapped to a Postgres type — BigQuery→PG, Postgres→PG, and **MySQL→PG since 1.18**
(`datetime`→`timestamp`, `double`→`double precision`, `json`→`jsonb`, blobs→`bytea`) — because
`columnTypes` define the bridge/extract table *in the warehouse*. (`--output` writes the file;
otherwise it prints to stdout.) It connects from your machine at build time, so it needs read
creds for the source in `.df-credentials.json` under `connections.<name>` (above). If the source
is not reachable while you work, hand-write the declaration from the known schema and tell the
user to regenerate it with `introspect` once it is.

## What `compile` auto-generates (you never write these)

In `fdw` mode, a foreign server **`<connection>_srv`** and a ref-able foreign table in schema
**`<connection>_ext`** — e.g. `bigquery_public_ext.zip_codes`; in `runner-extract` mode, a ref-able
**extract action** that materializes the same `<connection>_ext.<name>` table at run time.
Downstream models just `${ref("zip_codes")}` either one like any other source; multiple
declarations on one connection share the server. **Don't** hand-write a `wrapper`/foreign-table
action to read a source — named connections replace that manual path.

## Hand-over for a cross-warehouse project

Say which mode was chosen and why (runner-extract = materialized at run time, 1M-row/512MB cap;
fdw = live, needs `wrappers`), where the source creds go and in what shape, and that
`sqlanvil introspect <connection> <schema.table>` regenerates the declaration when columns change.

## Red flags

- a `connection:`-tagged declaration with **no** `columnTypes`, or hand-writing a foreign table /
  `CREATE SERVER` / `USER MAPPING` / `wrapper({...})` instead of using a named connection
- reading a connection from a `warehouse: bigquery`/`mysql` project (the read side must be
  `postgres`/`supabase`)
- `mode: fdw` on a `platform: mysql` connection (MySQL sources are runner-extract only — compile
  error), or `mode: fdw` on a database where you can't install extensions
- `columnTypes` written in the SOURCE dialect (`STRING`, `FLOAT64`, `datetime`, `double`) — they
  define the bridge/extract table IN THE WAREHOUSE, so they must be Postgres types
- a key file path (or the key itself) in `workflow_settings.yaml`, or a path where `credentials`
  wants the key's JSON content
