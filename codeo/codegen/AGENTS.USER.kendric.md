---
maintainer: starlight.ai
author: starlight.ai
version: v0.1.0
purpose: "User documentation for the kendric FTS5 search engine — CLI usage, programmatic API, scope filters, output format, MCP tools, multi-DB setup"
design_rationale: >
  For someone who uses kendric's public API to search OpenCode logs.
  Covers common patterns, error modes, and the full scope filter system.
  Does NOT cover internal architecture, FTS5 configuration, or SQL pipeline
  — those belong in AGENTS.CODER.kendric.md.
contact: David Bernat <david@starlight.ai>
changelog:
  - v0.1.0 ==> "open source by starlight - 20260721"
---

## Why This Module Exists

Kendric is a search engine for OpenCode's session logs. It indexes the
`part` table in your `opencode.db` into a contentless FTS5 sidecar and
lets you search across sessions, projects, and individual messages with
conversation window expansion. Results are enriched with the speaker role
(user vs assistant), the parent session ID (for subagent threads), and the
project worktree path.

Key files:
- `kendric.py` — core engine; importable from Python or runnable via CLI
- `kendric_mcp.py` — FastMCP server exposing five tools to any MCP client
- `task.project-state.md` — skill: analyze keywords into project-state YAML
- `task.session-cluster-by-intent.md` — skill: decompose search results into 5+ intent clusters
- `task.agent-forensic-diagnostic.md` — skill: per-thread design pattern forensic analysis
- `opencode_kendric.db` — the sidecar FTS5 index, auto-managed

## Scope & Use Cases

### Finding Past Work
- "What have we done with chromadb before?" — `search("chromadb")`
- "Which projects have discussed RAG?" — `search("RAG", worktree="Agentic")`
- "What sessions are subagents under session X?" — filter by `session_parent_id`
- "Show all my prompts from this week" — `speaker=user, date_from=20260714`

### Design Decisions
- "Why ChromaDB over in-process embedding?" — search with window to see the full debate
- "What trade-offs for scope-first vs FTS5-columns?" — the `session_id:ses_xxx AND rag` debate
- "Who proposed the COALESCE pattern?" — `speaker=user + "COALESCE"`
- "What was the final architecture for X?" — search with `n_window` to see resolution

### Context Building
- "What was the last thing in project X?" — `search("*", worktree="PhotoN", n_window=20)`
- "Give me the full thread around this part" — `get_surrounding_part_id("prt_xxx", n_window=30)`
- "Show me session Y's full conversation" — `search("*", session_id="ses_yyy", n_window=999)`
- "Reconstruct a tool call cycle" — find tool part_id, `get_context` captures step-start→tool→step-finish

### Debugging
- "What parameters did that tool call receive?" — search callID or tool name
- "What error was thrown?" — `search("Error", session_id="ses_xxx")`
- "When was this file first created?" — search filename, `date_from` unbounded

### Cross-Project Patterns
- "Which projects use Meilisearch vs ChromaDB?" — search both, compare `worktree`
- "What root-session conversations happened recently?" — post-filter `session_parent_id=NULL`
- "Which user's DB has the most about topic X?" — `_source_db` groups results
- "Are there cross-project standard design patterns?" — forensic diagnostic per thread

### Multi-User MCP
- "Show results from all users for topic X" — search across all SUPPORTED DBs
- "Which user has the most relevant context?" — compare `_source_db` distribution
- "Add a new user to the central search" — add to `SUPPORTED` dict, run `create_index`

## Why This Module Was Designed

OpenCode stores every session's conversation data in a local SQLite file.
There is no built-in way to search it across sessions, project boundaries,
or time ranges. Kendric fills that gap with a sidecar index that stays in
sync incrementally and requires zero configuration or background processes.

## Common Usage Patterns

### CLI: One-time setup

```bash
# Build the FTS5 index from all existing parts (~10s for 160k parts)
python kendric.py create

# Before each search session, sync new parts (<10ms)
python kendric.py update
```

### CLI: Search with filters

```bash
# Find all mentions of "chromadb" across all projects
python kendric.py search chromadb

# Scope to user messages only
python kendric.py search chromadb --speaker user

# Scope to a specific project (substring match on worktree path)
python kendric.py search chromadb --worktree Agentic

# Scope to a specific session
python kendric.py search chromadb --session-id ses_0f94a859dffeN42oMuF9lNhVFG

# Filter by part type and date range
python kendric.py search chromadb --part-type text --date-from 20260601 --date-to 20260701

# Show 5 parts of context before and after each match
python kendric.py search chromadb --n-window 5

# Combined:
python kendric.py search chromadb --speaker user --worktree Agentic --n-window 3
```

### Programmatic: Search

```python
from kendric import Kendric

# Search with scope filters
results = Kendric.search("chromadb", speaker="user", n_window=5)

# Each result is a dict with these keys:
for r in results:
    print(r["id"])          # "prt_xxx"
    print(r["session_id"])  # "ses_xxx"
    print(r["speaker"])     # "user" or "assistant"
    print(r["worktree"])    # "/home/.../Agentic"
    print(r["_source_db"])  # "/home/.../opencode.db"
    data = json.loads(r["data"])
    print(data["text"])     # the message text
```

### Programmatic: Context around a part

```python
from kendric import Kendric

# Given a part ID, get ±10 parts of conversational context
context = Kendric.get_surrounding_part_id("prt_f06e4d9dd001p05ghGxqw9Zqn3", n_window=10)

# Each result has the same enrichment columns as search()
```

### Programmatic: Lookup by ID

```python
from kendric import OpenCodeSQL

# Single ID -> dict or None
part = OpenCodeSQL.get_message_parts_by_ids("prt_f06e4d9dd001p05ghGxqw9Zqn3")

# Multiple IDs -> list[dict|None] (1:1 mapping)
parts = OpenCodeSQL.get_message_parts_by_ids(["prt_a", "prt_bad", "prt_c"])
# -> [dict, None, dict]
```

### Programmatic: Overlap search (intersection)

```python
from kendric import SearchUtilities

# Find part_ids appearing in both "TD Bank" and "Legal" context windows
pids = SearchUtilities.overlapping_search_results(
    ["TD Bank", "Legal"], n_window=20, at_least=2)
# at_least is clamped to min(len(queries), at_least)

# Get overlapping parts with full enrichment
parts = OpenCodeSQL.get_message_parts_by_ids(pids)
```

### Programmatic: Most recent sessions

```python
from kendric import SearchUtilities

sessions = SearchUtilities.most_recent_sessions("TD Bank", n_sessions=3)
for s in sessions:
    print(s["session_id"], s["match_count"], s["last_activity"])
```

### Skill: Project State Analysis

Use `task.project-state.md` to analyze a topic across sessions:

1. `overlap_search(["TD Bank", "Legal"])` → find overlapping part_ids
2. `get_context(part_id, n_window=20)` for each significant session
3. Produce YAML with intent_start, intent_end, roadblocks, branches, blockers, agile

### Skill: Session Cluster by Intent

Use `task.session-cluster-by-intent.md` to decompose search results:

1. `overlap_search(["TD Bank"], n_window=0, at_least=1)` → all part_ids
2. Group by session_id, examine first user prompt per session
3. Cluster into 5+ distinct intent groups
4. Produce YAML with title, description, reason, timeframe, pending

### MCP Server

```bash
# Start the FastMCP server
python kendric_mcp.py
```

The server exposes five tools:

| Tool | What it does | Returns |
|---|---|---|
| `search(query, speaker, worktree, ...)` | FTS5 search with scope filters | Formatted plain text, grouped by project then session, with user prompt previews |
| `get_context(part_id, n_window=20)` | ±N window around a part | Speaker-labeled thread with timestamps |
| `get_parts(ids)` | Lookup by comma-separated IDs | JSON dump with enrichment |
| `overlap_search(queries, n_window, at_least)` | Multi-query intersection | Formatted session summary with part counts |
| `most_recent_sessions(query, n_sessions)` | Last N sessions by activity | Formatted list with timestamps and counts |

The `search` tool returns a summary like:

```
Found 31 parts matching "chromadb" across 3 projects:

## Agentic (1 sessions)
  ses_0f94a85... (12 parts, parent=root)  e.g. "OK. Last part. We need a RAG..."

## PhotoN (1 sessions)
  ses_0da87df... (4 parts, parent=root)   e.g. "OK. InsightFace is our choice..."

## Qboot (1 sessions)
  ses_0f626e8... (2 parts, parent=root)   e.g. "OK. Do a quick grep on ~/code/..."
```

### Multi-DB Setup

To search multiple users' `opencode.db` files simultaneously, add them to
the `SUPPORTED` dict at the top of `kendric.py`:

```python
SUPPORTED = {
    SQL_PATH_OPENCODE: SQL_PATH_KENDRIC_OPENCODE,
    "/path/to/user2/opencode.db": "/path/to/user2/opencode_kendric.db",
}
```

Before copying an `opencode.db` from another machine, run:
```bash
sqlite3 path/to/opencode.db "PRAGMA wal_checkpoint(TRUNCATE);"
```
to merge WAL/shm temp files into the main DB. Only the `.db` file is needed.

## Scope Filters Reference

| CLI flag | `search()` param | Type | Behavior |
|---|---|---|---|
| `(positional)` | `query` | str | FTS5 MATCH query (AND, OR, NOT, NEAR, "phrase", prefix*) |
| `--speaker` | `speaker` | `str \| None` | `"user"` or `"assistant"` — exact match on message role |
| `--worktree` | `worktree` | `str \| None` | Substring match on project path: `"Agentic"` matches `/.../Agentic` |
| `--session-id` | `session_id` | `str \| None` | Exact match on session ID |
| `--message-id` | `message_id` | `str \| None` | Exact match on message ID |
| `--part-type` | `part_type` | `str \| None` | Exact match on `text/tool/reasoning/step-start/step-finish/patch/file` |
| `--date-from` | `date_from` | `str \| None` | YYYYMMDD inclusive start |
| `--date-to` | `date_to` | `str \| None` | YYYYMMDD inclusive end |
| `--n-window` | `n_window` | int | Parts before/after each match (default 5) |

## Output Format

Every result dict has these keys:

| Key | Type | Source | Example |
|---|---|---|---|
| `rowid` | int | SQLite implicit rowid | `163424` |
| `id` | str | part.id | `"prt_f84fc2e36001..."` |
| `message_id` | str | part.message_id | `"msg_f84fc2e34001..."` |
| `session_id` | str | part.session_id | `"ses_07b224c30ffe..."` |
| `time_created` | int | part.time_created (epoch ms) | `1784642547259` |
| `time_updated` | int | part.time_updated | `1784642547261` |
| `data` | str (JSON) | part.data | `{"type":"text","text":"..."}` |
| `speaker` | str \| None | message.data->'$.role' | `"user"` or `"assistant"` |
| `session_parent_id` | str \| None | session.parent_id | `"ses_xxx"` or `None` |
| `worktree` | str \| None | project.worktree | `"/home/.../Agentic"` |
| `_source_db` | str | SUPPORTED dict key | Full path to originating `opencode.db` |

## Error Modes

| Symptom | Cause | Resolution |
|---|---|---|
| `sqlite3.OperationalError: no such table: part_fts` | FTS5 index not created | Run `python kendric.py create` |
| `0 results` unexpectedly | `worktree` filter with None still applies `LIKE NULL` | Fixed in v0.0.7. Ensure `--worktree` is not passed without a value |
| `FTS5 tokenizer parse error` | Invalid tokenizer name in config | Check `KENDRIC_CONFIG.tokenizer` is one of the FTS5_TOKENIZERS |
| Slow search (>2s) | No scope filter provided; FTS5 scanning full 160k index | Narrow with `--session-id` or `--date-from`/`--date-to` |
| Missing `_source_db` | Old version (pre-v0.1.0) | Update to current version |

## Known Limitations

- **No FTS5 schema migration.** Changing tokenizer or detail settings
  requires dropping and recreating the index (`create_index()`).
- **Single-threaded.** All SQLite reads are sequential within each
  SUPPORTED DB iteration.
- **No incremental FTS5 column changes.** Adding a column to the FTS5
  virtual table requires a full rebuild.
- **`get_context` returns on first SUPPORTED match.** If the same part_id
  exists in multiple DBs (from backup restore), only the first is returned.
