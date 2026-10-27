# Migrating a whole Dataform project

Read this when the task is converting an existing Dataform/BigQuery project to sqlanvil. Don't
hand-translate file-by-file — run the converter, then work its report.

## The converter

`sqlanvil migrate-dataform <srcDir> <outDir>` (≥1.22) converts a project: the source dir is
READ-ONLY, declarations become per-project runner-extract connections, targets get safe rewrites
+ inline `SQLANVIL-MIGRATE:` markers, and `migration-report.{md,json}` carries the to-do list
(`validate` is the completion loop).

**Staying on BigQuery?** `--target-warehouse bigquery` (≥1.24) is the same-warehouse tooling
swap: SQL, `bigquery:{}` blocks, and declarations pass through untouched (no connections, no
dialect pass); `defaultProject`/`defaultLocation` carry through and dataset casing is preserved.
Since 1.26 the converter also generates the SECRETLESS ADC-mode `.df-credentials.json`
(`{projectId, location}` — local runs auth via `gcloud auth application-default login`, like
Dataform) and scaffolds an `environments.test` (`schemaSuffix: test`) — first real runs go through
`sqlanvil run . --environment test` so production datasets stay untouched; don't remove either
without asking.

## Migration is THREE phases, not two (≥1.28): convert → introspect → fix

`migrate-dataform` converts, the generated `scripts/introspect_all.sh` (run it on a machine with
read access to the sources) fills in each declaration's `columnTypes`, and then
**`sqlanvil migrate-fix <projectDir>`** does the conversions that are only possible once the
columns are known — `SELECT * EXCEPT (a, b)` expands to the explicit list, `GROUP BY ALL` becomes
positional ordinals. Running `migrate-fix` before introspect is the common mistake: it can't
resolve a star over a declaration with no `columnTypes`, so it reports the site instead of
rewriting it. `--dry-run` previews. Unresolved sites land in the report, never a half-rewrite,
with the reason (no `columnTypes`, an operations action whose columns must be listed by hand, an
unknown action). From 1.36.4 it removes the "run `sqlanvil migrate-fix`" marker over each site it
rewrites, so the ones left mark exactly the unresolved sites.

## The report is your to-do list, and it is written for you (an agent)

`migration-report.md` groups findings by CLASS (one item for all 141 `* EXCEPT` sites, not 141
items) and splits them into what you can apply mechanically vs. what needs a decision from the
human — bring those questions to them rather than guessing. Ordering is by consequence:
**changes-meaning ranks above fails-to-compile**, because a query that still runs and returns
different numbers is the failure nobody catches. All three phases write to the report, so re-read
it after each.

Work from the report, **not a grep for `SQLANVIL-MIGRATE:`**: a site inside a `js {}` block, a
`${…}` template or a multi-line string gets no inline marker (a comment there would change the
code; ≥1.36.3), but it is still in the report. From 1.37 the report also lists the BigQuery
no rule handles (`IFNULL`, `IF()`, `COUNTIF`, `DATETIME`, BigQuery-order `DATE_TRUNC`, `JSON_*`,
scripting, …), each with its PostgreSQL form. Work its `changes-meaning` classes FIRST — they
RUN on PostgreSQL and return different results: `REGEXP_REPLACE` without `'g'` (first match
only), `CONCAT`/`GREATEST`/`LEAST` over a possibly-NULL argument (PostgreSQL skips the NULL),
`LOG` (base and order), `EXTRACT(WEEK)`. BQML, JS UDFs, `FARM_FINGERPRINT` and BigQuery
`INFORMATION_SCHEMA` views are needs-decision: ask whether the feature moves at all. SQL built
inside JavaScript (`${…}` templates, `.js` files) is NOT scanned, so `sqlanvil validate` against
the warehouse is still the real completion check.

`introspect` maps a BigQuery `STRUCT` column to `jsonb` and a `REPEATED` field to an array
(`text[]`; a repeated STRUCT is one `jsonb` array) from 1.37; on older pins a STRUCT column fails
introspect and a REPEATED one comes out as its scalar type — set those by hand.

## Don't "fix" what the converter deliberately left (≥1.28)

`NOT ENFORCED` keys are rewritten to working PostgreSQL and left **commented** — uncommenting
enforces integrity the project has never had (and a FK also needs its referenced table's key
first). `ARRAY(SELECT AS STRUCT …)` is flagged with a recommended strategy (collapse / child table
/ jsonb) and the SQL to apply it, not rewritten — it restructures the data model, so it goes to
the human. Both are decisions, not oversights.

## Generated projects ship agent guidance (≥1.24)

`init` (all modes) and `migrate-dataform` write a warehouse-tailored `AGENTS.md` + a `CLAUDE.md`
bridge (`@AGENTS.md`) into the project — never overwriting existing ones. Read the project's own
AGENTS.md first when working in a generated repo.
