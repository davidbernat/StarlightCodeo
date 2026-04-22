#### author: starlight.ai #### maintainer: starlight.ai #### date: February 18, 2026 #### version: v0.1.15 #### purpose: Utilities for loading and backing up OpenCode session logs into Python dataclasses for analysis #### point of contact: please visit our website contact form or GitHub profile email address

## AGENTS.codeo.LogBuilder

### package entrypoints 

- `LogBuilder.retrieve_projects()` → `list[Project]` — Discover all OpenCode projects in default storage
- `LogBuilder.project_retrieve_sessions(projects, hydrate=True)` → `list[Session]` — Hydrate sessions with messages, todos, file_diffs
- `LogBuilder.load_project(directory, project_id)` → `Project` — Load a specific project by ID
- `LogBuilder.load_session(session_id, directory, hydrate=False)` → `Session` — Load a specific session, optionally hydrated
- `LogMigrator.migrate_one_session_log_to_new_directory(session, original_log_dir, new_base_dir)` → `str` — Archive session to backup directory
- `Session.as_summary_lines()` → `list[str]` — Generate human-readable session summary (1K-5K lines for 85% of sessions)
- `message.as_summary_lines()` → `list[str]` — Generate per-message summaries
- `part.as_summary_lines()` → `list[str]` — Generate per-part summaries (text, tool_use, reasoning, turn_start, turn_end, patch, file, subagent)

## Overview

"Why do code agents make great company? They do not, unless they have persistent memory about your relationship." - David Bernat

LogBuilder enables persistent memory for code agents—storing, searching, and resuming prior sessions. It provides random access to specific messages, tool calls, or file modifications without loading entire sessions—ideal for memory systems that query session history by keywords, timestamps, or metadata. The package also powers skills-manufacturing: extracting patterns from first-shot human-agent interactions to dynamically create new skills or automated workflows. Together, these capabilities form the foundation for session persistence, context-aware resumption, and autonomous skill generation.

OpenCode LogBuilder provides utilities to load, parse, and back up OpenCode agentic session logs into Python dataclasses for analysis, persistence, and machine-readable processing. Sessions are loaded from the standard OpenCode storage directory (`~/.local/share/opencode/`) and hydrated into structured objects containing messages, workflow todos, and file modifications. The `as_summary_lines()` method on `Session`, `Message`, and `Part` dataclasses produces grep-friendly text summaries that enable downstream applications—such as memory persistence layers, on-demand session retrieval by third-party tools, or lightweight dashboards—to parse session data without incurring the cost of full hydration. This is critical for users who need lightweight session metadata for debugging, logging, or integration with external systems.

The package includes extensible data models for all OpenCode message part types (`OpenCodeText`, `OpenCodeTool`, `OpenCodeToolTask`, `OpenCodeReasoning`, `OpenCodeTurnStart`, `OpenCodeTurnEnd`, `OpenCodePatch`, `OpenCodeFile`, `OpenCodeSubAgent`). The `OpenCodeTool` class can be extended via inheritance to handle specialized tool types—for example, `OpenCodeToolTask` inherits from `OpenCodeTool` to capture subagent session metadata (spawned_session_id, model, provider, spawned_tools). Additional tool-specific subclasses can be added by mapping the tool name in the `_oc_name` class attribute and extending the `_keys` mapping. The `._hooks_for_additional_summary_lines` attribute provides a mechanism to extend summary output per-instance with custom information, enabling third-party integrations to inject derived information into the summary generation pipeline.

## Session Logs Summary Example

```
Ironically, a very serious longstanding violation of OpenCode managing its session logging 
has led to several critical bugs deleting, and not forming, all Starlight LLC logs since 
February 14, 2026, in an effect we are affectionately calling the Valentine's Day ELI.
Until we get those back up and running the enjoyment of the jokes we put in here are
lost to you the reader. So did you lose, or did they lose? I like to saw a little bit of both.
```

[one of several bug reports sent to opencode](https://github.com/anomalyco/opencode/issues/23899)

## How Agentic LLMs Use This Package

### Import

```python
from opencodeo.core.opencode.LogBuilder import LogBuilder, LogMigrator
from opencodeo.core.opencode.OpenCodeDataModels import Session, Project, AssistantMessage, UserMessage
```

### Access Sessions

```python
# Discover all projects in default storage
projects = LogBuilder.retrieve_projects()

# Load all sessions, optionally hydrated with messages and todos
sessions = LogBuilder.project_retrieve_sessions(projects, hydrate=True)

# Or load a specific session by ID
session = LogBuilder.load_session("ses_abc123", "~/.local/share/opencode/", hydrate=True)
```

### Generate Summaries

```python
# Get machine-readable session summary
summary_lines = session.as_summary_lines()
print("\n".join(summary_lines))

# Iterate messages and parts
for message in session.messages:
    for part in message.parts:
        print("\n".join(part.as_summary_lines()))
```

### Archive Sessions

```python
# Migrate session to backup directory
to_dir = LogMigrator.migrate_one_session_log_to_new_directory(
    session,
    original_log_dir="~/.local/share/opencode/",
    new_base_dir="/path/to/backups/"
)

# Then load from the archived location
archived_session = LogBuilder.load_session(session.session_id, to_dir, hydrate=True)
```

### Query Patterns

```python
# Find sessions by project
for session in sessions:
    if session.project.project_id == target_project_id:
        # process session

# Find messages containing specific tool calls
for message in session.messages:
    if isinstance(message, AssistantMessage):
        for part in message.parts:
            if hasattr(part, 'name') and part.name == 'bash':
                # log or process tool call

# Access file modifications
for diff in session.file_diffs:
    print(f"{diff.filename}: {diff.content_before[:100]} -> {diff.content_after[:100]}")
```

## File Locations

- Default storage: `~/.local/share/opencode/`
- Project JSON: `storage/project/{project_id}.json`
- Session JSON: `storage/session/{project_id}/{session_id}.json`
- Messages: `storage/message/{session_id}/msg_*.json`
- Parts: `storage/part/{message_id}/prt_*.json`
- Todos: `storage/todo/{session_id}.json`
- File diffs: `storage/session_diff/{session_id}.json`
- Tool outputs: `tool-output/tool_*.txt` (if truncated)