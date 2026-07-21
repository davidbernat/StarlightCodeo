---
maintainer: starlight.ai
author: starlight.ai
version: v0.1.0
purpose: "Coder documentation for the kendric FTS5 search engine — internal architecture, SQL pipeline design, FTS5 trade-offs, and pattern rules"
design_rationale: >
  Covers kendric.py, kendric_mcp.py, and their sidecar FTS5 database.
  Design decisions, rejected alternatives, and gotchas from the maintainer's
  perspective. Assumes the reader has the full source available.
contact: David Bernat <david@starlight.ai>
changelog:
  - v0.1.0 ==> "open source by starlight - 20260721"
---

## Why This Module Exists

Kendric is a full-text search engine over OpenCode's SQLite log archive. It
reads the `part`, `message`, `session`, and `project` tables from one or more
`opencode.db` files via ATTACH, builds a contentless FTS5 inverted index in
a sidecar SQLite file (~10MB for 160k parts), and provides a scope-first
search pipeline that narrows rows via B-tree indexes before touching the
FTS5 MATCH operator. The search results are enriched with speaker role,
parent session ID, and project worktree via LEFT JOINs on primary keys.

Files in scope:
- `kendric.py` — core engine: `KendricConfig`, `Kendric.create_index()`,
  `Kendric.update_index()`, `Kendric.search()`,
  `Kendric.get_surrounding_part_id()`, `SearchUtilities`,
  `OpenCodeSQL`, `SQLiteUtilities`, `KENDRIC_CONFIG`
- `kendric_mcp.py` — FastMCP server wrapping search/get_context/get_parts/overlap_search/most_recent_sessions

## Why This Module Was Designed

OpenCode stores all session conversation data in a local SQLite file with no
built-in cross-session search capability. The original motivation was to turn
this archive into a searchable long-term memory — find past design decisions,
reconstruct conversation threads, and answer "what have we done with topic X?"
without manually scanning journal files.

The key design constraint: kendric must never modify opencode.db. All index
data lives in a sidecar database. The sidecar uses contentless FTS5 (stores
only the inverted index, no text copy) and is rebuilt incrementally via a
`last_rowid` sync tracker — no triggers, no schema changes to the OpenCode
database.

## Architecture and Dependency Flow

```
opencode.db  ──ATTACH──▶  kendric (FTS5 contentless)
  (user's DB)              kendric FTS5 sidecar
                                  │
  SUPPORTED dict ────────────── loop ──▶ each (main_db, fts_db)
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
            create_index   update_index    search / get_context
```

Dependency flow within `kendric.py`:

| File/Class | Responsibility | Depends on |
|---|---|---|
| `SQLiteUtilities` | Generic helpers: schema creation, date conversion | Nothing |
| `OpenCodeSQL` | OpenCode-specific logic: log parser, part ID lookup | `SQL_PATH_OPENCODE` |
| `Kendric` | Public API: create/update/search/context | `SQLiteUtilities`, `OpenCodeSQL`, `KENDRIC_CONFIG` |
| `KendricConfig` | FTS5 tokenizer/detail/prefix config | Pydantic |
| `KENDRIC_CONFIG` | Module-level singleton config instance | `KendricConfig` |
| `SUPPORTED` | Dict mapping opencode.db → FTS sidecar path | None (module constant) |

| `SearchUtilities` | Multi-query intersection, most-recent-session lookup | `Kendric`, `OpenCodeSQL` |

The `get_message_parts_by_ids` lookup on `OpenCodeSQL` searches all DBs in
`SUPPORTED` and merges results by part_id. It does NOT use FTS5 — pure SQL
on `oc.part WHERE id IN (...)`. The `_source_db` field identifies origin.

## Design Decisions and Rejected Alternatives

### FTS5 Mode: Contentless over External Content

External content mode (`content='tbl'`) lets FTS5 read from the source table
at query time with no data duplication. However, the content table must live
in the same SQLite database file as the FTS5 virtual table. Since kendric
uses a sidecar DB and ATTACHes the main DB, external content is impossible
across database boundaries.

Contentless (`content=''`) stores only the inverted index. When querying,
MATCH returns rowids — the caller must JOIN back to the source table to get
the actual data. This is exactly what the SEARCH_SQL pipeline does.

Result: ~8-15MB sidecar vs ~150-200MB for the raw part table. No duplication.

### Single-Column FTS5 over Multi-Column (scope-first)

Original design put `session_id`, `message_id`, `part_type`, `day` into
FTS5 as indexed columns, then used column filters in the MATCH query:
`session_id:ses_xxx AND rag`. The reasoning was that FTS5 would intersect
the inverted indexes internally before materializing results.

The user's counterargument: FTS5 column filter evaluation order is not
controllable. The `part` table already has B-tree indexes on `session_id`
and `message_id`. Narrowing via those indexes at the SQL level BEFORE
FTS5 MATCH is more predictable and equally fast.

Resolution: removed all FTS5 columns except `searchable_text`. All
structural filtering moved to SQL-level `COALESCE` patterns in the
`matched` CTE. The FTS5 is used only for full-text token matching.

### Positional Window over Temporal Window

Window expansion uses `ROW_NUMBER() OVER (ORDER BY time_created)` — a
positional window of ±N rows. A temporal window (±N ms) was considered
and rejected because: (1) varying density of conversation parts makes a
fixed time window unpredictable, (2) ROW_NUMBER() guarantees exactly the
requested number of context rows, (3) ordering by `time_created` (not
`rowid`) guarantees chronological correctness.

### COALESCE Filter Pattern

Every scope filter uses `col = COALESCE(?, col)`. When `?` is NULL,
`COALESCE(NULL, col) = col` → `col = col` → always true. This avoids
building dynamic WHERE clauses. Two exceptions use `(? IS NULL OR ...)`:
`worktree` (LIKE pattern) and `speaker` (json_extract). Both need two
param slots — one for the IS NULL guard, one for the value.

### Multi-DB via SUPPORTED Dict over Merge

Instead of merging multiple `opencode.db` files into one (INSERT OR
IGNORE), kendric iterates over a `SUPPORTED` dict at the top of the file.
Each user's DB gets its own FTS5 sidecar. Search results are concatenated
with a `_source_db` tag identifying origin. This avoids data
deduplication concerns (UUID collisions are impossible but merge logic
adds complexity) and lets each user manage their sidecar independently.

### SearchUtilities: Overlap Intersection

`overlapping_search_results` runs N searches, counts how many queries each
part_id appears in, then filters to those matching `at_least`. The threshold
is clamped to `min(len(queries), at_least)` so single-query calls with
`at_least=2` don't silently return nothing.

### Multi-DB get_message_parts_by_ids (Fixed v0.0.11)

Previous behavior returned results from the first SUPPORTED DB with any
match, silently dropping parts exclusive to other DBs. Fixed to collect
results from ALL DBs and merge by part_id. Critical for multi-user MCP.

## Gotchas (non-comprehensive)

**LIKE NULL silently drops rows.** `LIKE '%' || NULL || '%'` evaluates to
NULL, not FALSE. Combined with OR: `(col IS NULL OR LIKE NULL)` → `(FALSE
OR NULL)` → NULL — row silently excluded. Always use `(? IS NULL OR ...)`
with two param slots for LIKE filters. Fixed in v0.0.7.

**`rowid` is not in `SELECT *`.** SQLite's implicit `rowid` column is not
included in `SELECT p.*`. Every CTE and JOIN that needs rowid must select
it explicitly: `SELECT p.rowid, p.*`. This caused OperationalError in both
SEARCH_SQL and CONTEXT_SQL during development.

**`SELECT * FROM window` includes enrichment columns.** The `window` CTE
explicitly lists 6 base columns (`rowid, id, message_id, session_id,
time_created, time_updated, data`) plus 3 enrichment columns (`speaker,
session_parent_id, worktree`). `SELECT * FROM window` returns all 9. The
return type of `search()` is `list[dict]` and these are the dict keys.
If the window CTE columns change, callers must be updated.

**FTS5 contentless MATCH `*` returns all rowids.** When no query is
provided, `query.strip() or "*"` passes `"*"` to MATCH. In contentless
mode, this effectively returns every indexed rowid — which then gets
filtered by the scope COALESCE clauses. Works correctly but is worth
noting: `"*"` does not mean "match nothing".

**`get_message_parts_by_ids` merges across all SUPPORTED DBs.** If the same
part_id exists in two DBs (possible from backup restore), the second DB's
version overwrites the first in the merge dict. Part_ids are UUIDs so this
is practically impossible, but the behavior is documented.

## Pattern Rules

1. **Never add a column to the FTS5 virtual table without adding it to
   POPULATE_SQL.** The FTS5 column list and the INSERT SELECT must match.
   Both live in `kendric.py` at lines ~70-85. Change them together.

2. **All scope filters use COALESCE or IS NULL guard.** Adding a new filter
   param to `search()` requires: (a) a new `?` in the `matched` CTE WHERE
   clause, (b) the value in the `params` list at the correct position,
   (c) the parameter in the function signature, (d) the CLI flag in `main()`.
   Never use dynamic WHERE clause building — the fixed template approach
   is safer and easier to audit.

3. **No structural changes to opencode.db.** Kenric never writes to
   `opencode.db`. No CREATE, no INSERT, no UPDATE, no ALTER. The only
   exception is `OpenCodeSQL.get_message_parts_by_ids()` which reads only.
   If a feature requires writing to the user's DB, it does not belong in
   kendric.

## Testing Strategy

Kendric has no automated tests. Testing is done via CLI runs against the
user's real `opencode.db`. The `create_index` operation is self-verifying
(it reports row count and timing). `search` results are validated by
manual inspection of the JSON output. This is acceptable at current scale
but should be supplemented with:
- Unit tests for `_build_match_query` and `_epoch_ms` (pure function)
- Integration tests against a fixture `opencode.db` with known content
- Version migration tests (rebuilding index after schema changes)
