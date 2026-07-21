# maintainer: starlight.ai
# author: starlight.ai
# version v0.1.0
# contact: David Bernat <david@starlight.ai>
# purpose: FTS5 full-text search companion for OpenCode message parts
# changelog:
#  v0.1.0 ==> open source by starlight - 20260721

# Design rationale:
# - Contentless FTS5 (content="") stores only the inverted index (~8-15 MB sidecar
#   vs ~150-200 MB original part table). No text duplication.
# - Scope-first SQL: B-tree indexes on part.session_id/part.message_id narrow rows
#   to ~300 before FTS5 touch, avoiding full-index MATCH scans.
# - Single searchable_text column. All structural filters (session, type, date,
#   worktree, speaker) are SQL-level COALESCE/LIKE patterns on oc.part, not FTS5
#   column filters.
# - Porter+unicode61 tokenizer normalizes word forms (installing->install) for
#   better recall on conversational text.
# - Incremental sync via last_rowid tracker: copies only new parts (<10ms).
#   Full rebuild is ~10s for 160k parts. No triggers, no OpenCode schema touch.
# - Window expansion uses ROW_NUMBER() ordered by time_created, not rowid, so
#   chronological ordering is guaranteed.
# - Output enriched via LEFT JOINs: speaker from message.data, parent_id from
#   session, worktree from project. All on primary keys, only on narrowed rows.
# - Multi-DB via SUPPORTED dict: add power-user opencode.db paths for central
#   multi-user MCP. _source_db in return values identifies origin.

import argparse
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Globals
# ═══════════════════════════════════════════════════════════════════

OPENCODE_BASEDIR = Path.home() / ".local" / "share" / "opencode"
SQL_PATH_OPENCODE = str(OPENCODE_BASEDIR / "opencode.db")
SQL_PATH_KENDRIC_OPENCODE = str(OPENCODE_BASEDIR / "opencode.db.kendric.db")
SQL_PATH_OPENCODE_SILICON = str(OPENCODE_BASEDIR / "opencode.silicon.db")
SQL_PATH_KENDRIC_OPENCODE_SILICON = str(OPENCODE_BASEDIR/ "opencode.silicon.db.kendric.db")

# SUPPORTED: maps opencode.db path -> kendric FTS sidecar path.
# Add other users' DBs here for central multi-user search.
SUPPORTED = {SQL_PATH_OPENCODE: SQL_PATH_KENDRIC_OPENCODE,
             SQL_PATH_OPENCODE_SILICON: SQL_PATH_KENDRIC_OPENCODE_SILICON}


# ═══════════════════════════════════════════════════════════════════
# Kendric Config
# ═══════════════════════════════════════════════════════════════════

FTS5_TOKENIZERS = Literal["porter unicode61", "unicode61", "ascii", "trigram"]

class KendricConfig(BaseModel):
    """FTS5 tokenizer and index configuration. DB paths use SUPPORTED dict."""
    tokenizer: FTS5_TOKENIZERS = Field(default="porter unicode61", description="FTS5 tokenizer")
    detail: Literal["full", "column", "none"] = Field(default="full", description="FTS5 detail mode")
    prefix: str = Field(default="2 3", description="Prefix lengths for accelerated prefix queries")

KENDRIC_CONFIG = KendricConfig()


# ═══════════════════════════════════════════════════════════════════
# Schema SQL
# ═══════════════════════════════════════════════════════════════════
# All SQL templates are DB-agnostic. The calling function loops over SUPPORTED
# entries and ATTACHes each opencode.db as "oc".
# ═══════════════════════════════════════════════════════════════════

FTS5_CREATE = """\
CREATE VIRTUAL TABLE IF NOT EXISTS part_fts USING fts5(
    searchable_text, content='', tokenize='{tokenizer}', detail='{detail}', prefix='{prefix}'
)"""

SYNC_CREATE = "CREATE TABLE IF NOT EXISTS _sync (key TEXT PRIMARY KEY, value INTEGER)"

POPULATE_SQL = """\
INSERT INTO part_fts(rowid, searchable_text)
SELECT p.rowid, {expr}
FROM oc.part p {where}
ORDER BY p.rowid"""

SEARCH_SQL = """\
WITH matched AS (
    SELECT p.rowid, p.session_id, p.time_created
    FROM oc.part p
    JOIN oc.session s ON s.id = p.session_id
    LEFT JOIN oc.project j ON j.id = s.project_id
    LEFT JOIN oc.message m ON m.id = p.message_id
    WHERE p.session_id = COALESCE(?, p.session_id)
      AND p.message_id = COALESCE(?, p.message_id)
      AND json_extract(p.data, '$.type') = COALESCE(?, json_extract(p.data, '$.type'))
      AND p.time_created >= COALESCE(?, 0)
      AND p.time_created < COALESCE(?, 9223372036854775807)
      AND (? IS NULL OR j.worktree LIKE '%' || ? || '%')
      AND (? IS NULL OR json_extract(m.data, '$.role') = ?)
      AND p.rowid IN (SELECT rowid FROM part_fts WHERE part_fts MATCH ?)
),
session_ranked AS (
    SELECT p.rowid, p.id, p.message_id, p.session_id,
           p.time_created, p.time_updated, p.data,
           ROW_NUMBER() OVER (PARTITION BY p.session_id ORDER BY p.time_created) AS rn
    FROM oc.part p
    WHERE p.session_id IN (SELECT session_id FROM matched)
),
match_ranks AS (
    SELECT m.rowid, r.rn, r.session_id FROM matched m JOIN session_ranked r ON r.rowid = m.rowid
),
window AS (
    SELECT DISTINCT r.rowid, r.id, r.message_id, r.session_id,
           r.time_created, r.time_updated, r.data,
           json_extract(m.data, '$.role') AS speaker,
           s.parent_id AS session_parent_id,
           j.worktree AS worktree
    FROM match_ranks mr
    JOIN session_ranked r ON r.session_id = mr.session_id
        AND r.rn BETWEEN mr.rn - ? AND mr.rn + ?
    LEFT JOIN oc.message m ON m.id = r.message_id
    LEFT JOIN oc.session s ON s.id = r.session_id
    LEFT JOIN oc.project j ON j.id = s.project_id
)
SELECT * FROM window ORDER BY session_id, time_created
"""

CONTEXT_SQL = """\
WITH ranked AS (
    SELECT p.rowid, p.id, p.message_id, p.session_id,
           p.time_created, p.time_updated, p.data,
           ROW_NUMBER() OVER (PARTITION BY p.session_id ORDER BY p.time_created) AS rn
    FROM oc.part p
    WHERE p.session_id = (SELECT session_id FROM oc.part WHERE id = ?)
)
SELECT ranked.rowid, ranked.id, ranked.message_id, ranked.session_id,
       ranked.time_created, ranked.time_updated, ranked.data,
       json_extract(m.data, '$.role') AS speaker,
       s.parent_id AS session_parent_id,
       j.worktree AS worktree
FROM ranked
LEFT JOIN oc.message m ON m.id = ranked.message_id
LEFT JOIN oc.session s ON s.id = ranked.session_id
LEFT JOIN oc.project j ON j.id = s.project_id
WHERE ABS(ranked.rn - (SELECT rn FROM ranked WHERE id = ?)) <= ?
ORDER BY ranked.time_created
"""

GET_LAST_SYNC = "SELECT value FROM _sync WHERE key = 'last_rowid'"
SET_SYNC = "INSERT OR REPLACE INTO _sync(key, value) VALUES ('last_rowid', ?)"
GET_MAX_ROWID = "SELECT COALESCE(MAX(rowid), 0) FROM oc.part"


# ═══════════════════════════════════════════════════════════════════
# SQLiteUtilities — generic SQLite helpers
# ═══════════════════════════════════════════════════════════════════

class SQLiteUtilities:
    """Generic SQLite helpers: schema management, date conversion."""

    @staticmethod
    def ensure_matching_schema(fts_db: str):
        """Create FTS5 virtual table + sync tracking table if they don't exist."""
        with sqlite3.connect(fts_db) as conn:
            conn.execute(FTS5_CREATE.format(
                tokenizer=KENDRIC_CONFIG.tokenizer,
                detail=KENDRIC_CONFIG.detail,
                prefix=KENDRIC_CONFIG.prefix))
            conn.execute(SYNC_CREATE)

    @staticmethod
    def yyyymmss_str_to_ms(yyyymmdd: str, end_of_day: bool = False) -> int:
        """Convert YYYYMMDD to epoch milliseconds since 1970-01-01. end_of_day adds 24h."""
        dt = datetime.strptime(yyyymmdd, "%Y%m%d")
        if end_of_day: dt += timedelta(days=1)
        return int(time.mktime(dt.timetuple()) * 1000)


# ═══════════════════════════════════════════════════════════════════
# OpenCodeSQL — OpenCode-specific data knowledge
# ═══════════════════════════════════════════════════════════════════

class OpenCodeSQL:
    """OpenCode-specific logic: log parser for part.data JSON structure."""

    @staticmethod
    def logs_parser_message_parts() -> str:
        """Build SQL expression extracting searchable text from part.data JSON.

        Returns a SQL expression fragment like:
          COALESCE(json_extract(p.data, '$.text'),'') || ' ' || ...

        Only meaningful text fields are extracted. Structural JSON keys
        (type, callID, status, snapshot, etc.) are excluded to avoid token
        pollution in the FTS5 index.
        """
        paths = ["$.text", "$.state.input", "$.state.output", "$.tool"]
        parts = [f"COALESCE(json_extract(p.data, '{x}'), '')" for x in paths]
        return " || ' ' || ".join(parts)

    @staticmethod
    def get_message_parts_by_ids(ids: str | list[str]) -> dict | list[dict | None] | None:
        """Retrieve part rows by OpenCode ID(s). Returns 1:1 mapping by input order.

        Searches all DBs in SUPPORTED and merges results. _source_db field
        identifies which DB each result came from.

        Args:
            ids: Single ID string ("prt_xxx") or list of IDs.

        Returns:
            Single str: dict if found, None if not found.
            List: list[dict|None] — same order as input, None for any unmatched ID.
            Examples:
                get_message_parts_by_ids("prt_a") -> dict
                get_message_parts_by_ids("prt_missing") -> None
                get_message_parts_by_ids(["a","b"]) -> [dict, dict]
                get_message_parts_by_ids(["a","bad"]) -> [dict, None]
                get_message_parts_by_ids([]) -> []
        """
        single = isinstance(ids, str)
        if single: ids = [ids]
        placeholders = ",".join("?" for _ in ids)
        all_results: dict[str, dict] = {}
        for main_db in SUPPORTED:
            with sqlite3.connect(main_db) as conn:
                conn.row_factory = sqlite3.Row
                fetched = conn.execute(
                    f"SELECT rowid, * FROM part WHERE id IN ({placeholders})", ids).fetchall()
            for r in fetched:
                d = dict(r)
                d["_source_db"] = main_db
                all_results[r["id"]] = d
        result = [all_results.get(i) for i in ids]
        return result[0] if single else result


# ═══════════════════════════════════════════════════════════════════
# Kendric — public API
# ═══════════════════════════════════════════════════════════════════

class Kendric:
    """FTS5 search index management for OpenCode message parts.

    Usage:
        Kendric.create_index()                  # full rebuild on all SUPPORTED DBs
        Kendric.update_index()                  # incremental sync on all SUPPORTED DBs
        Kendric.search("rag", session_id="ses_xxx")  # search with context
        Kendric.get_surrounding_part_id("prt_xxx", n_window=10)  # context around a part

    SUPPORTED dict maps opencode.db -> kendric FTS sidecar for each user.
    Results include _source_db field identifying origin.
    """

    @staticmethod
    def _ensure_indexes_for(main_db: str, fts_db: str):
        """Create FTS5 table + sync table for one SUPPORTED entry, then populate."""
        logger.info(f"[kendric] Creating FTS5 index for {main_db} in {fts_db}")
        with sqlite3.connect(fts_db) as conn:
            conn.execute("DROP TABLE IF EXISTS part_fts")
            conn.execute(FTS5_CREATE.format(
                tokenizer=KENDRIC_CONFIG.tokenizer,
                detail=KENDRIC_CONFIG.detail,
                prefix=KENDRIC_CONFIG.prefix))
            conn.execute(SYNC_CREATE)

        with sqlite3.connect(fts_db) as fts:
            fts.execute(f"ATTACH DATABASE '{main_db}' AS oc")
            t0 = time.time()
            fts.execute(POPULATE_SQL.format(expr=OpenCodeSQL.logs_parser_message_parts(), where=""))
            n = fts.execute("SELECT changes()").fetchone()[0]
            max_r = fts.execute(GET_MAX_ROWID).fetchone()[0]
            fts.execute(SET_SYNC, [max_r])
            fts.commit()
            logger.info(f"[kendric] Indexed {n} parts in {time.time() - t0:.2f}s  last_rowid={max_r}")

    @staticmethod
    def create_index(skip_existing: bool = True):
        """Drop and recreate the FTS5 index for every DB in SUPPORTED.
        If skip_existing is True, entries with an existing _sync table are skipped.
        """
        for main_db, fts_db in SUPPORTED.items():
            if skip_existing:
                try:
                    with sqlite3.connect(fts_db) as conn:
                        if conn.execute("SELECT 1 FROM _sync LIMIT 1").fetchone():
                            logger.info(f"[kendric] Skipping {main_db} (index exists)")
                            continue
                except sqlite3.OperationalError:
                    pass  # _sync table doesn't exist yet — build it
            Kendric._ensure_indexes_for(main_db, fts_db)

    @staticmethod
    def update_index():
        """Incrementally sync new parts for every DB in SUPPORTED."""
        for main_db, fts_db in SUPPORTED.items():
            SQLiteUtilities.ensure_matching_schema(fts_db)
            with sqlite3.connect(fts_db) as fts:
                fts.execute(f"ATTACH DATABASE '{main_db}' AS oc")
                last = fts.execute(GET_LAST_SYNC).fetchone()
                if last is None:
                    logger.warning(f"[kendric] No sync state for {main_db} — running full create.")
                    fts.close()
                    Kendric._ensure_indexes_for(main_db, fts_db)
                    return
                last_rowid = last[0]
                max_rowid = fts.execute(GET_MAX_ROWID).fetchone()[0]
                if max_rowid <= last_rowid:
                    logger.info(f"[kendric] {main_db} up to date  last_rowid={last_rowid}")
                    continue
                fts.execute(POPULATE_SQL.format(expr=OpenCodeSQL.logs_parser_message_parts(), where="WHERE p.rowid > ?"), [last_rowid])
                n = fts.execute("SELECT changes()").fetchone()[0]
                fts.execute(SET_SYNC, [max_rowid])
                fts.commit()
                logger.info(f"[kendric] Synced {n} new parts for {main_db}  last_rowid={max_rowid}")

    @staticmethod
    def search(query: str, *, date_from: str | None = None,
               date_to: str | None = None, part_type: str | None = None,
               session_id: str | None = None, message_id: str | None = None,
               worktree: str | None = None, speaker: str | None = None,
               n_window: int = 5) -> list[dict]:
        """Full-text search over OpenCode parts with +/-n_window context per match.

        Args:
            query: FTS5 query (supports AND, OR, NOT, NEAR, "phrase", prefix*).
            date_from: YYYYMMDD inclusive start (None = unbounded).
            date_to: YYYYMMDD inclusive end (None = unbounded).
            part_type: text, tool, reasoning, step-start, etc.
            session_id: Scope to a single session.
            message_id: Scope to a single message.
            worktree: Substring match on project path (e.g. 'gitlab', 'Agentic').
            speaker: Filter by speaker role ('user' or 'assistant').
            n_window: Parts before/after each match.

        Returns:
            List of full part rows across all SUPPORTED DBs with _source_db field.
        """
        match_query = query.strip() if query and query.strip() else "*"
        date_from_ms = SQLiteUtilities.yyyymmss_str_to_ms(date_from) if date_from else None
        yyyymmss_str_to_ms = SQLiteUtilities.yyyymmss_str_to_ms(date_to, end_of_day=True) if date_to else None

        params = [
            session_id, message_id, part_type, date_from_ms, yyyymmss_str_to_ms,
            worktree, worktree,
            speaker, speaker,
            match_query,
            n_window, n_window,
        ]

        results = []
        for main_db, fts_db in SUPPORTED.items():
            SQLiteUtilities.ensure_matching_schema(fts_db)
            with sqlite3.connect(fts_db) as fts:
                fts.row_factory = sqlite3.Row
                fts.execute(f"ATTACH DATABASE '{main_db}' AS oc")
                for r in fts.execute(SEARCH_SQL, params).fetchall():
                    d = dict(r)
                    d["_source_db"] = main_db
                    results.append(d)
        return results

    @staticmethod
    def get_surrounding_part_id(part_id: str, n_window: int = 5) -> list[dict]:
        """Return +/-n_window parts around a specific part within its session.

        Searches each SUPPORTED DB in order. Returns on first match.
        """
        for main_db, fts_db in SUPPORTED.items():
            SQLiteUtilities.ensure_matching_schema(fts_db)
            with sqlite3.connect(fts_db) as fts:
                fts.row_factory = sqlite3.Row
                fts.execute(f"ATTACH DATABASE '{main_db}' AS oc")
                rows = fts.execute(CONTEXT_SQL, [part_id, part_id, n_window]).fetchall()
            if rows:
                result = [dict(r) for r in rows]
                for r in result:
                    r["_source_db"] = main_db
                return result
        return []


# ═══════════════════════════════════════════════════════════════════
# SearchUtilities — multi-query intersection
# ═══════════════════════════════════════════════════════════════════

class SearchUtilities:
    """Run multiple queries and find part_ids that overlap across them."""

    @staticmethod
    def overlapping_search_results(queries: list[str], n_window: int = 20, at_least: int = 2) -> list[str]:
        """Search each query with n_window context, return part_ids matching at_least queries.

        Searches run across all SUPPORTED DBs. A part_id counts toward the
        threshold if it appears in the window of at_least distinct queries.

        Args:
            queries: List of FTS5 query strings.
            n_window: Context window for each search.
            at_least: Minimum queries a part must match. Clamped to len(queries).

        Returns:
            List of part_id strings. Pass to get_surrounding_part_id().
        """
        from collections import Counter
        threshold = min(len(queries), at_least)
        counter: Counter[str] = Counter()
        for q in queries:
            results = Kendric.search(q, n_window=n_window)
            counter.update({r["id"] for r in results})
        return [pid for pid, count in counter.items() if count >= threshold]

    @staticmethod
    def most_recent_sessions(query: str, n_sessions: int = 1) -> list[dict]:
        """Return the most recent N sessions matching a query across all SUPPORTED DBs.

        Args:
            query: FTS5 query string.
            n_sessions: Number of sessions to return.

        Returns:
            List of dicts with session_id, worktree, _source_db, match_count, last_activity.
        """
        results = Kendric.search(query, n_window=0)
        from collections import defaultdict
        sessions: dict[str, dict] = {}
        for r in results:
            sid = r["session_id"]
            if sid not in sessions:
                sessions[sid] = {
                    "session_id": sid,
                    "worktree": r.get("worktree"),
                    "_source_db": r.get("_source_db"),
                    "match_count": 0,
                    "last_activity": 0,
                }
            sessions[sid]["match_count"] += 1
            sessions[sid]["last_activity"] = max(
                sessions[sid]["last_activity"], r.get("time_created", 0))
        sorted_sessions = sorted(
            sessions.values(), key=lambda s: -s["last_activity"])
        return sorted_sessions[:n_sessions]


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="FTS5 search over OpenCode message parts")
    p.add_argument("command", choices=["create", "update", "search"],
                   help="create=rebuild index, update=incremental sync, search=query")
    p.add_argument("query", nargs="?", default="", help="FTS5 query string (for search)")
    p.add_argument("--date-from", help="YYYYMMDD inclusive start date")
    p.add_argument("--date-to", help="YYYYMMDD inclusive end date")
    p.add_argument("--part-type", help="Filter by part type (text, tool, reasoning, etc.)")
    p.add_argument("--session-id", help="Scope to a specific session ID")
    p.add_argument("--message-id", help="Scope to a specific message ID")
    p.add_argument("--worktree", help="Filter by project path substring (e.g. 'gitlab', 'Agentic')")
    p.add_argument("--speaker", help="Filter by speaker role ('user' or 'assistant')")
    p.add_argument("--n-window", type=int, default=5,
                   help="Number of context parts before/after each match")
    p.add_argument("--no-skip-existing", action="store_true",
                   help="Force rebuild index for all SUPPORTED DBs (default: skip if exists)")
    args = p.parse_args()

    if args.command == "create":
        Kendric.create_index(skip_existing=not args.no_skip_existing)
    elif args.command == "update":
        Kendric.update_index()
    elif args.command == "search":
        if not args.query and not args.session_id and not args.part_type and not args.worktree and not args.speaker:
            p.error("search requires a query and/or --session-id/--part-type/--worktree/--speaker")
        results = Kendric.search(args.query, date_from=args.date_from,
                                  date_to=args.date_to, part_type=args.part_type,
                                  session_id=args.session_id, message_id=args.message_id,
                                  worktree=args.worktree, speaker=args.speaker,
                                  n_window=args.n_window)
        print(json.dumps(results, default=str, indent=2))


if __name__ == "__main__":
    main()
