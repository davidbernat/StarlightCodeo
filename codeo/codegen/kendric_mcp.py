# maintainer: starlight.ai
# author: starlight.ai
# version v0.1.0
# contact: David Bernat <david@starlight.ai>
# purpose: FastMCP server exposing kendric search/context/parts/overlap as MCP tools
# changelog:
#  v0.1.0 ==> open source by starlight - 20260721

# Design rationale:
# - Thin MCP wrapper around kendric.Kendric static methods.
# - Returns formatted plain-text so the calling agent can present results
#   conversationally without post-processing.
# - search groups by worktree then session_id so the agent can ask
#   "which session?" — the start of a REPL flow.
# - get_context returns a speaker-labeled thread for forensic diagnostic.
# - get_parts is a debugging/lookup utility.
# - No LongPersistentTask or servlet — these are instant-return tools.

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from kendric import Kendric, OpenCodeSQL, SearchUtilities

mcp = FastMCP("starlight-codegen-kendric")


@mcp.tool()
async def search(query: str, speaker: str | None = None,
                 worktree: str | None = None, part_type: str | None = None,
                 session_id: str | None = None, message_id: str | None = None,
                 date_from: str | None = None, date_to: str | None = None,
                 n_window: int = 0) -> str:
    """Full-text search across OpenCode message parts with scope filters.

    Args:
        query: FTS5 query (supports AND, OR, NOT, NEAR, "phrase", prefix*).
        speaker: "user" or "assistant" to filter by speaker role.
        worktree: Substring match on project path (e.g. "Agentic").
        part_type: "text", "tool", "reasoning", "step-start", etc.
        session_id: Scope to a single session.
        message_id: Scope to a single message.
        date_from: YYYYMMDD inclusive start (None = unbounded).
        date_to: YYYYMMDD inclusive end (None = unbounded).
        n_window: Parts before/after each match (0 = direct matches only).

    Returns:
        Results grouped by worktree then session_id, with match counts and
        user prompt previews. Use get_context() to expand a specific part.
    """
    results = Kendric.search(query, speaker=speaker, worktree=worktree,
                              part_type=part_type, session_id=session_id,
                              message_id=message_id, date_from=date_from,
                              date_to=date_to, n_window=n_window)

    if not results:
        return f"No results for query={query!r}."

    by_worktree: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in results:
        wt = Path(r.get("worktree", "") or "/").name or r["session_id"][:30]
        by_worktree[wt][r["session_id"]].append(r)

    lines = [f"Found {len(results)} parts matching {query!r} across {len(by_worktree)} projects:\n"]
    for wt in sorted(by_worktree):
        sessions = by_worktree[wt]
        lines.append(f"\n## {wt} ({len(sessions)} sessions)")
        for sid in sorted(sessions, key=lambda s: -len(sessions[s])):
            parts = sessions[sid]
            user_prompts = [p for p in parts if p.get("speaker") == "user"]
            preview = ""
            if user_prompts:
                t = json.loads(user_prompts[0]["data"]).get("text", "")
                preview = f"  e.g. \"{t[:100].strip()}\""
            parent = parts[0].get("session_parent_id") or "root"
            lines.append(
                f"  {sid[:35]} ({len(parts)} parts, parent={parent[:30]}){preview}")

    return "\n".join(lines)


@mcp.tool()
async def get_context(part_id: str, n_window: int = 20) -> str:
    """Return +/-n_window parts around a specific part within its session.

    Args:
        part_id: Part ID like "prt_xxx".
        n_window: Number of parts before and after the target.

    Returns:
        Formatted thread with speaker, timestamp, and text per line.
    """
    results = Kendric.get_surrounding_part_id(part_id, n_window=n_window)
    if not results:
        return f"Part {part_id} not found."

    lines = [f"Context for {part_id} (+/-{n_window}):\n"]
    for r in results:
        d = json.loads(r["data"])
        text = d.get("text", d.get("tool", "")).strip()
        ts = datetime.fromtimestamp(r["time_created"] / 1000).strftime("%H:%M:%S")
        label = f"[{r.get('speaker','?')[:4]}] {ts}"
        if d.get("type") in ("step-start", "step-finish"):
            label = f"[───] {ts}  ({d['type']})"
        lines.append(f"{label}  {text[:200]}")
    return "\n".join(lines)


@mcp.tool()
async def get_parts(ids: str) -> str:
    """Retrieve full part rows by comma-separated OpenCode part IDs.

    Args:
        ids: Comma-separated part IDs like "prt_xxx,prt_yyy".

    Returns:
        JSON dump of the requested parts with speaker, session_parent_id, worktree.
    """
    parts = OpenCodeSQL.get_message_parts_by_ids([i.strip() for i in ids.split(",")])
    return json.dumps(parts, default=str, indent=2)


@mcp.tool()
async def overlap_search(queries: list[str], n_window: int = 20, at_least: int = 2) -> str:
    """Search multiple queries and return part_ids appearing in at_least of them.

    Args:
        queries: List of FTS5 queries like ["TD Bank", "Chambers AND Commerce"].
        n_window: Context window for each search.
        at_least: Minimum queries a part must match to be included.

    Returns:
        Formatted list of overlapping part_ids grouped by session, or message if none found.
    """
    pids = SearchUtilities.overlapping_search_results(queries, n_window=n_window, at_least=at_least)
    if not pids:
        return f"No part_ids matched at_least={at_least} of the queries."

    parts = [p for p in OpenCodeSQL.get_message_parts_by_ids(pids) if p is not None] if pids else []
    if not parts:
        return "No parts found for overlapping IDs."

    by_session: dict[str, list[str]] = defaultdict(list)
    for p in parts:
        by_session[p.get("session_id", "?")].append(p["id"])
    lines = [f"Found {len(pids)} overlapping part_ids (at_least={at_least}, n_window={n_window}):\n"]
    for sid in sorted(by_session):
        lines.append(f"  {sid[:35]} ({len(by_session[sid])} parts)")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()


@mcp.tool()
async def most_recent_sessions(query: str, n_sessions: int = 1) -> str:
    """Return the most recent N sessions matching a query.

    Args:
        query: FTS5 query string.
        n_sessions: Number of sessions to return.

    Returns:
        Formatted list of sessions with match count and last activity time.
    """
    sessions = SearchUtilities.most_recent_sessions(query, n_sessions=n_sessions)
    if not sessions:
        return f"No sessions found for query={query!r}."
    from datetime import datetime
    lines = [f"Most recent {len(sessions)} sessions for {query!r}:\n"]
    for s in sessions:
        ts = datetime.fromtimestamp(s["last_activity"] / 1000).strftime("%Y-%m-%d %H:%M")
        wt = Path(s.get("worktree", "") or "/").name or "?"
        lines.append(f"  {s['session_id'][:35]}  {wt}  ({s['match_count']} matches, last {ts})")
    return "\n".join(lines)
