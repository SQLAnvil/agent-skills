# SQLAnvil Agent Skills

[Agent Skills](https://agentskills.io) for working with [SQLAnvil](https://sqlanvil.com) — the open-source SQL workflow engine (a Dataform fork) with first-class PostgreSQL, Supabase, and MySQL/MariaDB support.

## Skills

| Skill | What it does |
|---|---|
| [`sqlanvil-engineering-fundamentals`](skills/sqlanvil-engineering-fundamentals/SKILL.md) | The delta guide for authoring sqlanvil data projects. Your Dataform/BigQuery instincts are mostly right — this skill covers exactly where they aren't: config blocks, credentials shape, statement separators, numeric enums, CLI verbs, cross-warehouse connections, and the MySQL inversions. |

## Install

With the [skills CLI](https://skills.sh) (installs into Claude Code, Codex CLI, Cursor, and other Agent-Skills-compatible tools it detects):

```bash
npx skills add SQLAnvil/agent-skills
```

Manual install for any spec-compliant agent: copy (or symlink) `skills/sqlanvil-engineering-fundamentals/` into your agent's skills directory — e.g. `~/.claude/skills/` for Claude Code.

## Related

- **Docs**: https://sqlanvil.com/docs
- **Engine**: https://github.com/SQLAnvil/sqlanvil (`npm i -g @sqlanvil/cli`)
- **SQLAnvil Cloud**: https://app.sqlanvil.com — hosted CI/scheduled runs, with a remote MCP server at `https://app.sqlanvil.com/api/mcp` for operating and debugging runs (the skill covers *authoring*; the MCP server covers *operating*).

## Versioning

The skill tracks engine releases — it pins the current `sqlanvilCoreVersion` and documents features by the release that introduced them (e.g. `≥1.22`). It is updated as part of every SQLAnvil release.

## License

[Apache-2.0](LICENSE), matching the SQLAnvil engine.
