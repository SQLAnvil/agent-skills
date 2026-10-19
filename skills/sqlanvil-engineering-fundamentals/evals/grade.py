#!/usr/bin/env python3
"""
Objective grader for one eval run of sqlanvil-engineering-fundamentals.

    python3 grade.py <run-dir>            # e.g. .../eval-1-.../with_skill/run-1
    python3 grade.py <run-dir> --project <dir>   # if auto-detection picks the wrong dir

Reads <run-dir>/eval_metadata.json, finds the produced project under <run-dir>/outputs/
(the first directory holding workflow_settings.yaml or dataform.json), compiles it with
the installed `sqlanvil` CLI (`compile --json`, saved to <run-dir>/compiled.json), and
settles every assertion tagged `[auto:<check> args...]` from the compiled graph plus the
source files. Untagged assertions are graded by a human (or a grader subagent) from
outputs/transcript.md: put their verdicts in <run-dir>/manual_grades.json as
{"<assertion text>": {"passed": true, "evidence": "..."}} and re-run; until then they
are written as passed=null so the gap is visible.

Writes <run-dir>/grading.json in the skill-creator schema (expectations[] = {text,
passed, evidence}, summary, timing). Pass/fail is decided by the ENGINE wherever
possible: an index declared in config shows up as postgres.indexes in the graph, a
hand-rolled one shows up as CREATE INDEX text in postOps, and a `when(!incremental())`
guard shows up as the statement being present in postOps but absent from
incrementalPostOps. That is a stronger check than grepping the .sqlx.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE_EXT = {".sqlx", ".js", ".yaml", ".yml", ".json"}
SKIP_DIRS = {"node_modules", "target", ".git", "__pycache__"}
TAG = re.compile(r"^\[auto:([a-z_]+)((?:\s+[^\s\]]+)*)\]\s*")

PG_ONLY_TYPES = re.compile(r"^(string|float64|int64|geography|bytes|datetime|double|bool)$", re.I)
BQ_ISMS = [
    (r"bigquery\s*:\s*\{", "a bigquery: {} block"),
    (r"\bpartitionBy\b", "partitionBy"),
    (r"\bclusterBy\b", "clusterBy"),
    (r"\bOPTIONS\s*\(", "OPTIONS(...)"),
    (r"bigqueryPolicyTags", "bigqueryPolicyTags"),
    (r"(?m)^\s*defaultProject\s*:", "defaultProject"),
    (r"(?m)^\s*defaultLocation\s*:", "defaultLocation"),
    (r"dataformCoreVersion", "dataformCoreVersion"),
    (r"`[\w-]+\.[\w-]+\.[\w-]+`", "a backticked project.dataset.table reference"),
]
FDW_HANDROLL = [r"CREATE\s+SERVER", r"FOREIGN\s+TABLE", r"IMPORT\s+FOREIGN\s+SCHEMA", r"USER\s+MAPPING",
                r"CREATE\s+EXTENSION", r"\bwrapper\s*\("]


# ---------------------------------------------------------------- context

class Ctx:
    def __init__(self, run_dir: Path, project: Path | None):
        self.run_dir = run_dir
        self.outputs = run_dir / "outputs"
        self.project = project or find_project(self.outputs)
        self.sources: dict[str, str] = {}
        self.graph = None
        self.compile_errors: list[str] = []
        self.compile_stderr = ""
        if self.project:
            for p in sorted(self.project.rglob("*")):
                if p.is_file() and p.suffix in SOURCE_EXT and not (SKIP_DIRS & set(p.relative_to(self.project).parts)):
                    try:
                        self.sources[str(p.relative_to(self.project))] = p.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        pass
            self.compile()

    # -- compile -----------------------------------------------------------
    def compile(self):
        if self.project is None:
            self.compile_stderr = "no project directory found under outputs/"
            return
        if not shutil.which("sqlanvil"):
            self.compile_stderr = "sqlanvil CLI not on PATH"
            return
        settings = self.project / "workflow_settings.yaml"
        if not settings.exists():
            self.compile_stderr = "no workflow_settings.yaml (dataform.json present)" if (self.project / "dataform.json").exists() else "no workflow_settings.yaml"
            return
        r = subprocess.run(["sqlanvil", "compile", str(self.project), "--json"], capture_output=True, text=True, timeout=300)
        self.compile_stderr = r.stderr
        try:
            self.graph = json.loads(r.stdout)
        except json.JSONDecodeError:
            self.graph = None
            self.compile_stderr += "\n(stdout was not JSON)\n" + r.stdout[-2000:]
            return
        (self.run_dir / "compiled.json").write_text(json.dumps(self.graph, indent=1))
        ge = self.graph.get("graphErrors") or {}
        self.compile_errors = [f"{e.get('fileName', '?')}: {e.get('message', '')}" for e in ge.get("compilationErrors", [])]

    # -- graph helpers -----------------------------------------------------
    def tables(self):
        return (self.graph or {}).get("tables", []) or []

    def table(self, name):
        for t in self.tables():
            if t.get("target", {}).get("name") == name:
                return t
        return None

    def operations(self):
        return (self.graph or {}).get("operations", []) or []

    def assertions(self):
        return (self.graph or {}).get("assertions", []) or []

    def declarations(self):
        return (self.graph or {}).get("declarations", []) or []

    def extracts(self):
        return (self.graph or {}).get("extracts", []) or []

    def warehouse(self):
        return ((self.graph or {}).get("projectConfig") or {}).get("warehouse") or settings_value(self, "warehouse")

    def table_ops(self, t, create_path=True, incremental_path=True):
        out = []
        if create_path:
            out += (t.get("preOps") or []) + (t.get("postOps") or [])
        if incremental_path:
            out += (t.get("incrementalPreOps") or []) + (t.get("incrementalPostOps") or [])
        return [strip_comments(s) for s in out]

    def all_ddl(self):
        """Every statement sqlanvil would run besides the model queries themselves."""
        out = []
        for t in self.tables():
            out += self.table_ops(t)
        for o in self.operations():
            out += [strip_comments(q) for q in o.get("queries") or []]
        return out

    def source_text(self, exts=(".sqlx", ".js", ".yaml", ".yml")):
        return "\n".join(f"-- {k}\n{v}" for k, v in self.sources.items() if Path(k).suffix in exts)

    def sources_matching(self, pattern, exts=(".sqlx", ".js", ".yaml", ".yml")):
        rx = re.compile(pattern, re.I | re.M)
        return [k for k, v in self.sources.items() if Path(k).suffix in exts and rx.search(v)]

    def creds(self):
        for name in (".df-credentials.json",) + tuple(sorted(k for k in self.sources if re.match(r"\.df-credentials.*\.json$", Path(k).name))):
            if name in self.sources:
                try:
                    return name, json.loads(self.sources[name])
                except json.JSONDecodeError as e:
                    return name, {"__parse_error__": str(e)}
        return None, None


def find_project(outputs: Path):
    if not outputs.exists():
        return None
    for marker in ("workflow_settings.yaml", "dataform.json"):
        hits = sorted(p.parent for p in outputs.rglob(marker) if not (SKIP_DIRS & set(p.relative_to(outputs).parts)))
        if hits:
            return hits[0]
    return None


def settings_value(ctx, key):
    txt = ctx.sources.get("workflow_settings.yaml", "")
    m = re.search(rf"(?m)^{key}\s*:\s*(\S+)", txt)
    return m.group(1).strip("'\"") if m else None


def strip_comments(sql):
    """Drop -- and /* */ comments: agents annotate their DDL, and a comment that says
    'multiple primary keys' or contains a ';' must not trip a check."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", "", sql)


def strip_bodies(sql):
    """Remove $$...$$ and BEGIN...END bodies so an internal ';' doesn't count as a separator."""
    sql = strip_comments(sql)
    sql = re.sub(r"\$\$.*?\$\$", "$$BODY$$", sql, flags=re.S)
    sql = re.sub(r"'(?:[^']|'')*'", "'STR'", sql, flags=re.S)
    sql = re.sub(r"\bBEGIN\b.*?\bEND\b", "BEGIN BODY END", sql, flags=re.S | re.I)
    return sql


# ---------------------------------------------------------------- checks
# Each check returns (passed: bool, evidence: str). Args come from the tag.

CHECKS = {}


def check(fn):
    CHECKS[fn.__name__] = fn
    return fn


def need_graph(ctx):
    if ctx.graph is None:
        return (False, f"project did not compile to a graph: {ctx.compile_stderr.strip()[-300:] or 'no project found'}")
    return None


@check
def compiles(ctx):
    if not ctx.project:
        return False, "no project directory with workflow_settings.yaml under outputs/"
    if (ctx.project / "dataform.json").exists():
        return False, "project uses dataform.json (sqlanvil reads workflow_settings.yaml)"
    if ctx.graph is None:
        return False, f"compile produced no graph: {ctx.compile_stderr.strip()[-400:]}"
    if ctx.compile_errors:
        return False, f"{len(ctx.compile_errors)} compilation error(s): " + " | ".join(ctx.compile_errors[:3])
    n = len(ctx.tables()) + len(ctx.operations()) + len(ctx.assertions())
    return True, f"compiled clean: {len(ctx.tables())} dataset(s), {len(ctx.operations())} operation(s), {len(ctx.assertions())} assertion(s) ({n} actions) with sqlanvil {ctx.graph.get('sqlanvilCoreVersion', '?')}"


@check
def settings(ctx, warehouse):
    txt = ctx.sources.get("workflow_settings.yaml")
    if txt is None:
        return False, "no workflow_settings.yaml"
    problems = []
    if not re.search(rf"(?m)^warehouse\s*:\s*['\"]?{warehouse}['\"]?\s*(#.*)?$", txt):
        problems.append(f"no flat top-level `warehouse: {warehouse}` (found: {settings_value(ctx, 'warehouse')!r})")
    if not re.search(r"(?m)^sqlanvilCoreVersion\s*:", txt):
        problems.append("sqlanvilCoreVersion not pinned")
    for bad in ("defaultProject", "defaultLocation", "dataformCoreVersion"):
        if re.search(rf"(?m)^\s*{bad}\s*:", txt):
            problems.append(f"carries {bad}")
    if problems:
        return False, "; ".join(problems)
    return True, f"warehouse: {warehouse}, sqlanvilCoreVersion: {settings_value(ctx, 'sqlanvilCoreVersion')}, no BigQuery fields"


@check
def creds_flat(ctx, kind):
    name, c = ctx.creds()
    if c is None:
        return False, "no .df-credentials*.json in the project"
    if "__parse_error__" in c:
        return False, f"{name} is not valid JSON: {c['__parse_error__']}"
    allowed = {"host", "port", "database", "user", "password", "sslMode"}
    if kind in ("postgres", "supabase"):
        allowed |= {"defaultSchema"}
    extra_maps = {"connections", "storage"}
    keys = set(c.keys())
    problems = []
    nested = [k for k in keys - extra_maps if isinstance(c[k], dict)]
    if nested:
        problems.append(f"nested object under {nested} (should be flat)")
    for bad in ("username", "databaseName", "ssl", "postgres", "mysql", "projectId"):
        if bad in keys:
            problems.append(f"uses `{bad}`")
    unknown = keys - allowed - extra_maps
    if unknown and not nested:
        problems.append(f"unknown field(s) {sorted(unknown)}")
    for req in ("host", "user", "database"):
        if req not in keys:
            problems.append(f"missing `{req}`")
    if kind == "mysql" and "defaultSchema" in keys:
        problems.append("`defaultSchema` is Postgres-only (MysqlConnection has none)")
    if kind == "supabase":
        if "supabase" not in str(c.get("host", "")):
            problems.append(f"host {c.get('host')!r} is not a Supabase host")
        if c.get("sslMode") != "require":
            problems.append(f"sslMode is {c.get('sslMode')!r}, Supabase needs \"require\"")
    if problems:
        return False, f"{name}: " + "; ".join(problems)
    return True, f"{name} is flat with keys {sorted(keys)}"


@check
def no_bigquery_isms(ctx):
    hits = []
    for pat, label in BQ_ISMS:
        files = ctx.sources_matching(pat)
        if files:
            hits.append(f"{label} in {files[:3]}")
    if (ctx.project / "dataform.json").exists() if ctx.project else False:
        hits.append("dataform.json present")
    if hits:
        return False, "; ".join(hits)
    return True, f"none of {len(BQ_ISMS)} BigQuery-ism patterns found in {len(ctx.sources)} source files"


@check
def index_in_config(ctx, table, method=None, opclass=None):
    if (g := need_graph(ctx)):
        return g
    t = ctx.table(table)
    if not t:
        return False, f"no action named {table} in the graph ({[x['target']['name'] for x in ctx.tables()]})"
    block = t.get("postgres") or {}
    idx = block.get("indexes") or []
    problems = []
    if not idx:
        problems.append(f"{table} has no postgres.indexes (postgres block = {json.dumps(block)[:200]})")
    if method and not any(str(i.get("method", "")).upper() == method.upper() for i in idx):
        problems.append(f"no index with method {method} (methods: {[i.get('method', 'btree') for i in idx]})")
    if opclass:
        ok = [i for i in idx if i.get("opclass") == opclass]
        if not ok:
            problems.append(f"no index with opclass == {opclass!r} as a single string ({[i.get('opclass') for i in idx]})")
    handrolled = [s.strip()[:80] for s in ctx.all_ddl() if re.search(r"CREATE\s+(UNIQUE\s+)?INDEX", s, re.I)]
    if handrolled:
        problems.append(f"hand-written CREATE INDEX in operations: {handrolled[:2]}")
    if problems:
        return False, "; ".join(problems)
    return True, f"{table}.postgres.indexes = {json.dumps(idx)[:300]}; no CREATE INDEX in any operation"


@check
def partition_in_config(ctx, table, kind, column, min_bounds="1"):
    if (g := need_graph(ctx)):
        return g
    t = ctx.table(table)
    if not t:
        return False, f"no action named {table}"
    part = (t.get("postgres") or {}).get("partition") or (t.get("mysql") or {}).get("partition")
    problems = []
    if not part:
        problems.append(f"{table} has no partition in its postgres/mysql block")
    else:
        if str(part.get("kind", "RANGE")).upper() != kind.upper():
            problems.append(f"kind is {part.get('kind')!r}, wanted {kind}")
        if column not in (part.get("columns") or []) and column not in str(part.get("expression", "")):
            problems.append(f"partition columns {part.get('columns')} lack {column}")
        if len(part.get("partitions") or []) < int(min_bounds):
            problems.append(f"only {len(part.get('partitions') or [])} bound(s), wanted >= {min_bounds}")
        if not part.get("includeDefault"):
            problems.append("includeDefault not set")
    handrolled = [s.strip()[:80] for s in ctx.all_ddl() if re.search(r"PARTITION\s+(BY|OF)", s, re.I)]
    if handrolled:
        problems.append(f"hand-written partition DDL: {handrolled[:2]}")
    if problems:
        return False, "; ".join(problems)
    return True, f"{table}.partition = {json.dumps(part)[:300]}"


@check
def matview(ctx, name, *flags):
    if (g := need_graph(ctx)):
        return g
    t = ctx.table(name)
    if not t:
        return False, f"no action named {name}"
    problems = []
    if t.get("type") != "view" or not t.get("materialized"):
        problems.append(f"{name} is type={t.get('type')} materialized={t.get('materialized')} (wanted view + materialized: true)")
    pg = t.get("postgres") or {}
    if "in_place" in flags and pg.get("refreshPolicy") != "on_dependency_change":
        problems.append(f"postgres.refreshPolicy is {pg.get('refreshPolicy')!r}, wanted on_dependency_change")
    if "unique_index" in flags and not any(i.get("unique") for i in pg.get("indexes") or []):
        problems.append(f"no unique index in postgres.indexes ({pg.get('indexes')})")
    if "mysql" in flags:
        if pg:
            problems.append(f"postgres block on a mysql model: {pg}")
        src = ctx.sources.get(t.get("fileName", ""), "")
        for bad in ("refreshPolicy", "noData"):
            if bad in src:
                problems.append(f"{bad} is Postgres-only")
    handrolled = [s.strip()[:80] for s in ctx.all_ddl() if re.search(r"CREATE\s+MATERIALIZED\s+VIEW", s, re.I)]
    if handrolled:
        problems.append(f"hand-written CREATE MATERIALIZED VIEW: {handrolled[:1]}")
    if problems:
        return False, "; ".join(problems)
    return True, f"{name}: type=view materialized=true, postgres={json.dumps(pg)[:200]}, mysql={json.dumps(t.get('mysql'))[:100]}"


@check
def operations_split(ctx, min_queries="2"):
    if (g := need_graph(ctx)):
        return g
    if ctx.sources_matching(r"^\s*DELIMITER\b"):
        return False, f"DELIMITER directive in {ctx.sources_matching(r'^\s*DELIMITER\b')[:2]} (client-only; sqlanvil splits on ---)"
    cands = [o for o in ctx.operations() if any(re.search(r"\b(FUNCTION|PROCEDURE)\b", q, re.I) for q in o.get("queries") or [])]
    if not cands:
        return False, f"no operations action creating a FUNCTION/PROCEDURE (operations: {[o['target']['name'] for o in ctx.operations()]})"
    o = cands[0]
    qs = o.get("queries") or []
    problems = []
    if len(qs) < int(min_queries):
        problems.append(f"{o['target']['name']} compiled into {len(qs)} statement(s), wanted >= {min_queries} (split on ---)")
    for q in qs:
        if ";" in strip_bodies(q).rstrip().rstrip(";"):
            problems.append(f"a `;` outside a $$/BEGIN body in: {q.strip()[:80]!r}")
            break
    if problems:
        return False, "; ".join(problems)
    return True, f"{o['target']['name']}: {len(qs)} statements: {[q.strip()[:50] for q in qs]}"


@check
def declaration(ctx, qualified, model):
    if (g := need_graph(ctx)):
        return g
    schema, _, name = qualified.partition(".")
    decl = [d for d in ctx.declarations() if d.get("target", {}).get("name") == name and (not schema or d["target"].get("schema") == schema)]
    problems = []
    if not decl:
        problems.append(f"no declaration {qualified} in the graph ({[(d['target'].get('schema'), d['target'].get('name')) for d in ctx.declarations()]})")
    t = ctx.table(model)
    if not t:
        problems.append(f"no model {model}")
    else:
        deps = [(d.get("schema"), d.get("name")) for d in t.get("dependencyTargets") or []]
        if (schema or None, name) not in deps and name not in [n for _, n in deps]:
            problems.append(f"{model} does not depend on {qualified} (deps: {deps})")
        src = ctx.sources.get(t.get("fileName", ""), "")
        body = src.split("}", 1)[-1] if "config" in src else src
        if "${ref(" not in src or re.search(rf"\b(from|join)\s+\"?{schema}\"?\.\"?{name}\"?", body, re.I):
            problems.append(f"{model} hard-codes {qualified} instead of ${{ref()}}")
    if problems:
        return False, "; ".join(problems)
    return True, f"{qualified} declared; {model} depends on it via ref()"


@check
def columns_documented(ctx, *flags):
    if (g := need_graph(ctx)):
        return g
    missing = []
    for t in ctx.tables():
        if "skip_views" in flags and t.get("type") == "view":
            continue
        d = t.get("actionDescriptor") or {}
        if not d.get("description") or not d.get("columns"):
            missing.append(f"{t['target']['name']} (description={bool(d.get('description'))}, columns={len(d.get('columns') or [])})")
    if missing:
        return False, "undocumented: " + ", ".join(missing)
    return True, f"all {len(ctx.tables())} dataset(s) have description + columns"


@check
def assertions_declared(ctx, table, unique_col, nonnull_col):
    if (g := need_graph(ctx)):
        return g
    mine = [a for a in ctx.assertions() if (a.get("parentAction") or {}).get("name") == table]
    uq = [a for a in mine if re.search(rf"GROUP BY\s+\"?{unique_col}\"?", a.get("query", ""), re.I) and "index_row_count" in a.get("query", "")]
    nn = [a for a in mine if re.search(rf"\b{nonnull_col}\b\s+IS\s+NOT\s+NULL", a.get("query", ""), re.I)]
    problems = []
    if not uq:
        problems.append(f"no uniqueKey assertion on {table}.{unique_col}")
    if not nn:
        problems.append(f"no nonNull assertion on {table}.{nonnull_col}")
    if problems:
        return False, "; ".join(problems) + f" (assertions for {table}: {[a['target']['name'] for a in mine]})"
    return True, f"{[a['target']['name'] for a in uq + nn]}"


@check
def incremental_upsert(ctx, table, key, filter_col=None):
    if (g := need_graph(ctx)):
        return g
    t = ctx.table(table)
    if not t:
        return False, f"no action named {table}"
    problems = []
    if t.get("type") != "incremental":
        problems.append(f"type is {t.get('type')}")
    if (t.get("uniqueKey") or []) != [key]:
        problems.append(f"uniqueKey is {t.get('uniqueKey')}, wanted [{key!r}]")
    if filter_col:
        iq, q = t.get("incrementalQuery") or "", t.get("query") or ""
        if iq.strip() == q.strip() or filter_col not in iq:
            problems.append(f"incremental query has no {filter_col} filter distinct from the create query (when(incremental()) missing?)")
    if problems:
        return False, "; ".join(problems)
    return True, f"{table}: incremental, uniqueKey={t.get('uniqueKey')}, incremental-only filter on {filter_col}"


@check
def pk_guarded(ctx, table):
    if (g := need_graph(ctx)):
        return g
    t = ctx.table(table)
    if not t:
        return False, f"no action named {table}"
    create = "\n".join(ctx.table_ops(t, incremental_path=False))
    incr = "\n".join(ctx.table_ops(t, create_path=False))
    if not re.search(r"PRIMARY\s+KEY", create, re.I):
        return False, f"no ADD PRIMARY KEY in the create-path pre/post operations of {table}"
    if re.search(r"PRIMARY\s+KEY", incr, re.I):
        return False, "ADD PRIMARY KEY also runs on the incremental path (not wrapped in when(!incremental())), so the 2nd run fails"
    return True, "PRIMARY KEY in postOps (create path) and absent from incrementalPostOps"


@check
def rls_policy(ctx, table, *predicate):
    if (g := need_graph(ctx)):
        return g
    pred = re.sub(r"\s+", "", " ".join(predicate)).lower()
    bad = ctx.sources_matching(r"type\s*:\s*[\"']rlsPolicy[\"']", exts=(".sqlx",))
    if bad:
        return False, f"`type: \"rlsPolicy\"` in a .sqlx config ({bad}) - Supabase actions are JS calls / actions.yaml entries"
    pols = []
    for o in ctx.operations():
        for q in o.get("queries") or []:
            if re.search(r"create\s+policy", q, re.I) and table in q:
                pols.append(q)
    if not pols:
        return False, f"no generated CREATE POLICY on {table} in the graph (operations: {[o['target']['name'] for o in ctx.operations()]})"
    hit = [p for p in pols if pred in re.sub(r"\s+", "", p).lower()]
    if not hit:
        return False, f"policy found but USING clause lacks `{' '.join(predicate)}`: {pols[0][:160]!r}"
    return True, f"generated: {hit[0][:160]!r}"


@check
def rls_enabled(ctx, table):
    if (g := need_graph(ctx)):
        return g
    t = ctx.table(table)
    if t and (t.get("supabase") or {}).get("enableRls"):
        return True, f"{table}.supabase.enableRls = true"
    for o in ctx.operations():
        for q in o.get("queries") or []:
            if re.search(r"enable\s+row\s+level\s+security", q, re.I) and table in q:
                return True, f"generated: {q.strip()[:120]!r}"
    for s in ctx.table_ops(t) if t else []:
        if re.search(r"enable\s+row\s+level\s+security", s, re.I):
            return True, f"in {table} post_operations: {s.strip()[:120]!r}"
    return False, f"nothing enables RLS on {table} (no supabase.enableRls, no generated/raw ENABLE ROW LEVEL SECURITY)"


@check
def mysql_block(ctx, table, *flags):
    if (g := need_graph(ctx)):
        return g
    t = ctx.table(table)
    if not t:
        return False, f"no action named {table}"
    my = t.get("mysql") or {}
    idx = my.get("indexes") or []
    problems = []
    if not my:
        problems.append(f"{table} has no mysql: block")
    if "fulltext" in flags and not any(str(i.get("type", "")).lower() == "fulltext" for i in idx):
        problems.append(f"no index with type fulltext in mysql.indexes ({idx})")
    if "prefix" in flags and not any(re.search(r"\(\d+\)", c) for i in idx for c in i.get("columns") or []):
        problems.append(f"no prefix-length column like \"title(50)\" in mysql.indexes ({idx})")
    for f in flags:
        if f.lower() in ("innodb", "myisam", "aria") and str(my.get("engine", "")).lower() != f.lower():
            problems.append(f"engine is {my.get('engine')!r}, wanted {f}")
    pgb = ctx.sources_matching(r"postgres\s*:\s*\{")
    if pgb:
        problems.append(f"postgres: block in a mysql project ({pgb[:2]})")
    handrolled = [s.strip()[:80] for s in ctx.all_ddl() if re.search(r"CREATE\s+(UNIQUE\s+|FULLTEXT\s+|SPATIAL\s+)?INDEX", s, re.I)]
    if handrolled:
        problems.append(f"hand-written CREATE INDEX: {handrolled[:2]}")
    if problems:
        return False, "; ".join(problems)
    return True, f"{table}.mysql = {json.dumps(my)[:300]}"


@check
def no_manual_unique(ctx, table):
    if (g := need_graph(ctx)):
        return g
    t = ctx.table(table)
    if not t:
        return False, f"no action named {table}"
    hits = [s.strip()[:80] for s in ctx.table_ops(t) + [strip_comments(q) for o in ctx.operations() for q in o.get("queries") or [] if table in q]
            if re.search(r"(PRIMARY\s+KEY|UNIQUE\s+(INDEX|KEY)|ADD\s+UNIQUE)", s, re.I)]
    if hits:
        return False, f"hand-added key for the merge: {hits[:2]} (the adapter creates uq_<db>_<table> for uniqueKey)"
    return True, f"no PRIMARY KEY / UNIQUE DDL on {table}; uniqueKey={t.get('uniqueKey')}"


@check
def no_double_quoted_identifiers(ctx):
    if (g := need_graph(ctx)):
        return g
    rx = re.compile(r'"[A-Za-z_][A-Za-z0-9_]*"\s*\.\s*"?[A-Za-z_]|\.\s*"[A-Za-z_][A-Za-z0-9_]*"')
    hits = [s.strip()[:80] for s in ctx.all_ddl() if rx.search(s)]
    if hits:
        return False, f"double-quoted identifiers in raw DDL (MySQL uses backticks): {hits[:2]}"
    return True, f"no double-quoted identifiers across {len(ctx.all_ddl())} DDL statement(s)"


@check
def connection(ctx, *kv):
    want = dict(p.split("=", 1) for p in kv)
    conns = ((ctx.graph or {}).get("projectConfig") or {}).get("connections") or {}
    txt = ctx.sources.get("workflow_settings.yaml", "")
    if not conns and re.search(r"(?m)^connections\s*:", txt):
        conns = {"(unparsed)": {}}
    if not conns:
        return False, "no connections: block in workflow_settings.yaml"
    for name, c in conns.items():
        if c.get("platform") == want.get("platform", c.get("platform")):
            mism = {k: (c.get(k), v) for k, v in want.items() if str(c.get(k)) != v}
            if mism:
                return False, f"connection {name}: {mism} (got/wanted)"
            if re.search(r"private_key|BEGIN PRIVATE KEY|\"type\"\s*:\s*\"service_account\"", txt):
                return False, "service-account key material inlined in workflow_settings.yaml"
            if re.search(r"(?m)^\s*(credentials|keyFile|keyFilename|password|accessToken)\s*:", txt):
                return False, "credentials in workflow_settings.yaml (they belong under connections.<name> in .df-credentials.json)"
            return True, f"connection {name} = {json.dumps(c)}"
    return False, f"no connection with platform {want.get('platform')} ({list(conns)})"


@check
def creds_connections(ctx):
    name, c = ctx.creds()
    if c is None:
        return False, "no .df-credentials*.json"
    conns = c.get("connections")
    if not isinstance(conns, dict) or not conns:
        return False, f"{name} has no connections map (keys: {sorted(c.keys())})"
    # The key material itself may legitimately be absent (the eval machine has no key file, and
    # `credentials` wants the key's JSON content, so an honest agent leaves it for the user and
    # says how in the hand-over, which the hand-over check verifies). What must be true here:
    # the source has an entry in the creds file, and nothing secret leaked into workflow_settings.
    for cname, cc in conns.items():
        if not isinstance(cc, dict):
            continue
        auth = [k for k in ("credentials", "keyFilename", "keyFile", "accessToken", "impersonateServiceAccount", "password") if k in cc]
        if isinstance(cc.get("credentials"), str) and cc["credentials"].strip().startswith(("~", "/", ".")):
            return False, f"{name}: connections.{cname}.credentials is a file path ({cc['credentials']!r}); the field takes the key's JSON content"
        return True, f"{name}: connections.{cname} present with {auth or 'no key material (left for the user)'}; warehouse keys {sorted(k for k in c if k not in ('connections', 'storage'))}"
    return False, f"{name}: connections {list(conns)} has no object entry"


@check
def declaration_connection(ctx, name):
    if (g := need_graph(ctx)):
        return g
    cands = [e for e in ctx.extracts() if (e.get("target") or {}).get("name") == name or e.get("name") == name]
    cands += [d for d in ctx.declarations() if d.get("connection") and (d.get("target") or {}).get("name") == name]
    if not cands:
        return False, f"no connection-tagged declaration / extract named {name} (extracts: {[(e.get('target') or {}).get('name') or e.get('name') for e in ctx.extracts()]})"
    e = cands[0]
    cols = e.get("columnTypes") or e.get("columns") or {}
    if isinstance(cols, list):
        cols = {c.get("name", str(i)): c.get("type", "") for i, c in enumerate(cols)}
    if not cols:
        src = ctx.sources_matching(rf"name\s*:\s*[\"']{name}[\"']")
        m = re.search(r"columnTypes\s*:\s*\{([^}]*)\}", "\n".join(ctx.sources.get(s, "") for s in src), re.S)
        if m:
            cols = dict(re.findall(r"(\w+)\s*:\s*[\"']([^\"']+)[\"']", m.group(1)))
    if not cols:
        return False, f"{name} has no columnTypes (needed to build the extract table)"
    bad = {k: v for k, v in cols.items() if PG_ONLY_TYPES.match(v.strip())}
    if bad:
        return False, f"columnTypes in the SOURCE dialect, not Postgres: {bad}"
    return True, f"{name}: connection={e.get('connectionName') or e.get('connection')}, columnTypes={cols}"


@check
def no_fdw_handrolled(ctx):
    hits = []
    for pat in FDW_HANDROLL:
        f = ctx.sources_matching(pat, exts=(".sqlx", ".js"))
        if f:
            hits.append(f"{pat} in {f[:2]}")
    if hits:
        return False, "; ".join(hits)
    return True, "no CREATE SERVER / FOREIGN TABLE / USER MAPPING / CREATE EXTENSION / wrapper() in sources"


@check
def depends_on(ctx, model, dep):
    if (g := need_graph(ctx)):
        return g
    t = ctx.table(model)
    if not t:
        return False, f"no action named {model}"
    deps = [f"{d.get('schema')}.{d.get('name')}" for d in t.get("dependencyTargets") or []]
    if not any(dep in d for d in deps):
        return False, f"{model} dependencyTargets {deps} do not include {dep}"
    return True, f"{model} depends on {[d for d in deps if dep in d]}"


# ---------------------------------------------------------------- driver

def grade(run_dir: Path, project: Path | None):
    meta = json.loads((run_dir / "eval_metadata.json").read_text())
    manual_path = run_dir / "manual_grades.json"
    manual = json.loads(manual_path.read_text()) if manual_path.exists() else {}
    ctx = Ctx(run_dir, project)
    expectations = []
    for text in meta["assertions"]:
        m = TAG.match(text)
        if m:
            name, args = m.group(1), m.group(2).split()
            fn = CHECKS.get(name)
            if not fn:
                passed, evidence = False, f"unknown check {name}"
            else:
                try:
                    passed, evidence = fn(ctx, *args)
                except Exception as e:  # a crashed check is a failed check, with the reason
                    passed, evidence = False, f"check {name} crashed: {e!r}"
        elif text in manual:
            passed, evidence = manual[text]["passed"], manual[text].get("evidence", "")
        else:
            passed, evidence = None, "manual: grade from outputs/transcript.md and add to manual_grades.json"
        expectations.append({"text": text, "passed": passed, "evidence": evidence})
    graded = [e for e in expectations if e["passed"] is not None]
    passed = sum(1 for e in graded if e["passed"])
    out = {
        "expectations": expectations,
        "summary": {"passed": passed, "failed": len(graded) - passed, "total": len(expectations),
                    "ungraded": len(expectations) - len(graded),
                    "pass_rate": round(passed / len(expectations), 3) if expectations else 0},
        "project": str(ctx.project) if ctx.project else None,
        "compile": {"ok": ctx.graph is not None and not ctx.compile_errors, "errors": ctx.compile_errors[:10],
                    "stderr_tail": ctx.compile_stderr.strip()[-500:]},
    }
    # Timing stays in the sibling timing.json: skill-creator's aggregate_benchmark.py reads
    # tokens only from there, and skips it when grading.json carries its own timing block.
    (run_dir / "grading.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--project", type=Path)
    ap.add_argument("--list-checks", action="store_true")
    args = ap.parse_args()
    if args.list_checks:
        for k in CHECKS:
            print(k)
        return
    out = grade(args.run_dir.resolve(), args.project)
    s = out["summary"]
    print(f"{args.run_dir}: {s['passed']}/{s['total']} passed, {s['failed']} failed, {s['ungraded']} ungraded; project={out['project']}")
    for e in out["expectations"]:
        mark = {True: "PASS", False: "FAIL", None: "----"}[e["passed"]]
        print(f"  {mark}  {e['text'][:90]}\n        {e['evidence'][:220]}")
    sys.exit(0 if s["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
