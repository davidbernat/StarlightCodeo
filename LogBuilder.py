# author: starlight.ai
# maintainer: starlight.ai
# date: February 18, 2026
# version: v0.1.15
# purpose: Utilities for loading and backing up OpenCode session logs into Python dataclasses for analysis
# point of contact: please visit our website contact form or GitHub profile email address

"""
OpenCode Session Log Loader and Backup Utilities

Extensibility: The OpenCodeTool class can be extended via inheritance to handle specialized tool types. OpenCodeToolTask
is provided as an example, inheriting from OpenCodeTool to capture the unique metadata (spawned_session_id, model, provider, 
spawned_tools) when the tool.task spawns a subagent session. Additional tool-specific subclasses can be added by mapping 
the tool name in the _oc_name class attribute and extending the _keys mapping as needed.

The ._hooks_for_additional_summary_lines attribute provides a mechanism to extend summary output per-instance with custom 
information. These hooks are called during as_summary_lines() generation and receive the part instance and the current 
summary lines list, allowing third-party integrations to inject derived information. The ._print_third_party class attribute 
allows specifying tool names whose output should be included in summaries, enabling integration with proprietary MCP tools.

The .as_summary_lines() method generates a text-based summary of each dataclass instance, producing a list of strings 
suitable for logging, debugging, or persistence to memory systems. This is critical for downstream applications that need 
session summaries without loading the full session object, such as memory persistence layers, on-demand session retrieval 
by third-party tools, or lightweight dashboards that display session metadata without incurring the cost of full hydration.

Architecture Overview:

    LogMigrator (backup utilities)
    └── migrate_one_session_log_to_new_directory(session, original_log_dir, new_base_dir)

    LogBuilder (high-level API)
    ├── retrieve_projects() -> list[Project]
    ├── project_retrieve_sessions(projects, hydrate=True) -> list[Session]
    │   └── calls LogBuilderBackend methods for each session
    ├── load_project(directory, project_id) -> Project
    └── load_session(session_id, directory, hydrate=False) -> Session
        └── calls LogBuilderBackend methods if hydrate=True

    LogBuilderBackend (internal utilities)
    ├── subprocess_run(cmd) -> str               # executes shell commands via subprocess
    ├── simple_recursive_filename_matches(directory, pattern) -> list[str]  # find files recursively
    ├── import_filename_into_opencode_data_model(filename, cls) -> dataclass  # parse JSON to dataclass
    ├── import_filename_into_list_of_opencode_data_model(filename, cls) -> list[dataclass]  # parse JSON array
    ├── profile_and_filter(items) -> list        # filter and log statistics
    ├── session_retrieve_todos(session) -> Session
    ├── session_retrieve_file_diffs(session) -> Session
    ├── sessions_retrieve_messages(session) -> Session
    │   └── message_retrieve_parts(message) -> Message
    │       └── get_message_parts_filenames(message_id) -> list[str]
    ├── get_session_message_filenames(session) -> list[str]
    └── get_project_id_from_session_id(directory, session_id) -> Project
        └── load_project() (via LogBuilder)

Usage:
    # Load all projects and sessions from default OpenCode storage
    from LogBuilder import LogBuilder
    projects = LogBuilder.retrieve_projects()
    sessions = LogBuilder.project_retrieve_sessions(projects, hydrate=True)

    # Load a specific session
    session = LogBuilder.load_session("ses_abc123", "~/.local/share/opencode/", hydrate=True)

    # Load a session from a backup directory (migrated session)
    session = LogBuilder.load_session("ses_abc123", "/path/to/backups/project_id/", hydrate=True)

    # Migrate session to backup directory
    from LogBuilder import LogMigrator
    LogMigrator.migrate_one_session_log_to_new_directory(
        session,
        original_log_dir="~/.local/share/opencode/",
        new_base_dir="/path/to/backups/"
    )

Data Models:
    Session      - Complete session with messages, todos, file_diffs, if hydrated
    Project      - Root directory where OpenCode runs
    UserMessage  - User prompt with title and file_diffs
    AssistantMessage - Assistant response with tokens, cost, parts
    SessionToDo  - Workflow tasks created during session
    FileModification - Single file edit with before/after content
    OpenCodePartMessageJson - Base class for message parts
        ├── OpenCodeText      - Text content
        ├── OpenCodeTool     - Tool invocation
        ├── OpenCodeToolTask - Subagent task (spawns new session)
        ├── OpenCodeReasoning - Internal thought process
        ├── OpenCodeTurnStart/End - Turn markers
        ├── OpenCodePatch    - Files modified
        ├── OpenCodeFile    - File attachments
        └── OpenCodeSubAgent - Agent references


# This package underserves git, hashes, and sandboxes, for now.
# These are extracted to a degree to summarize sessions effectively, but only partially extracted from the JSON output.
#   - sandboxes: The Project.sandboxes field has an intentionally empty _keys mapping in OpenCodeDataModels.py as we do
#       not yet know what dataset maps to sandbox environments (e.g., Daytona, which we are likely to use internally)
#   - git snapshots: TurnStart/TurnEnd contain snapshot hashes but we do not presently extract actual snapshot content
#       from storage/snapshot/. There is little to no documentation as to what these snapshots refer to, especially as
#       each TurnStart/TurnEnd contains a hash, implying that commits are, or are intended, to be created with each
#       OpenCode agent message. We see no evidence of this being configured, either, nor would we permit this, leaving
#       us very confused what standard operating procedure is to be expected when dealing with these datatypes and logs.
#   - file hashes: FileModification does not include git file hashes, only content_before/after
#       We do this to minimize space in succinct log summaries because the state of git snapshots is further unknown.
"""
from opencodeo.core.opencode.OpenCodeDataModels import Project, AssistantMessage, UserMessage, FileModification, \
    Session, SessionToDo, recursive_constructor, hydrate_message_part_from_str, \
    sort_assistant_message_parts_approximately, sort_user_message_parts_approximately
from pathlib import Path
import subprocess
import shutil
import time
import json
import os

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# TODO: remaining steps, test migration, create a few last test runs, copy tool-outputs.
# post tomorrow morning or late tonight.


# FIXME: "Did opencode remove step-start timestamp?" "Did opencode change the type names in the last several days?"
# FIXME: Are IDs apportioned in any sorting logic, i.e., organizing prt_*.json in AssistantMessage in their sequences?
# FIXME: "How does OpenCode make git snapshots (in snapshot/) and when are they deleted/managed?"
# FIXME: "Haven't I seen OpenCode search for git commits in the local directory, where these would not be?"
# FIXME: "Did OpenCode remove timestamp from step-finish?"
# FIXME: What exactly are the tool-calls/ mode, and how do you expect to incorporate them into their logs?
# FIXME: Is it presumed that the logs are assigned IDs by a sortable rubric (presumed)
# FIXME: What exactly is snapshot/ and how does one archive for sustainability as is engineered into OpenCode logs?
# FIXME: What is tool_*? Is the * equal to the call_id in message.part?
# FIXME: the prt_* information in tool.task summary appear to be entirely fictitious (i.e., not filesystem at all)
# FIXME: What is the schema for Attachments? Is type != file ever possible? Why are these not saved to disk?!
# NOTE: this must mean that its differences are compiled and presented by the opencode agent itself to the TUI
# FRUSTRATION: this is very inconvenient as a summary of this information would be invaluable, FIXME: do summary
# TODO Unknowns / Gripes: What do "role=user agent=explore" combinations mean when users cannot select them?
# TODO Unknowns / Gripes: Should user.file_diffs = cumulative assistant.file_diffs of all assistant messages that follow? and granularity?
# TODO Unknowns / Gripes: Are actions/messages not attributed to _TODOs?
# TODO: Where does Compactifying come from? This appears to be a per-turn dynamic process internal to the OpenCode workflow engine? Transparency?
# TODO Unknowns / Gripes: mode always equals agent — appears legacy/redundant
# TODO: path.root should be redundant with project directory. difference?
# TODO tokens.reasoning is often 0 or 1, but can be very large, i.e., 120, or 320 — what does magnitude signify?
# TODO: cache.read / cache.write tokens etc. — what do these measure exactly?
# TODO: assistant messages do not have a summary field (i.e., title & diff) which is a real major bother shame
# TODO: subagent is listed as relative/path/to/subagent/name but agent is listed as name - why the difference? indicator?

##########################################################################################
# QUESTIONS FOR OPENCODE DEVELOPMENT TEAM
# The following questions were identified during deep analysis of OpenCodeDataModels.py
# and represent information gaps that would improve transparency and usability.
##########################################################################################

# A. Turn/Message Structure
# Q1: Are git snapshots actually created on every turn (TurnStart/TurnEnd), or is this field empty/placeholder?
# Q2: Did OpenCode remove the timestamp from step-start? (Previously present, now absent)
# Q3: What specifically triggers a turn end? Is "tool-calls" the only reason?
# Q4: What is the sorting logic for prt_*.json files within an AssistantMessage?

# B. Tool Output & Truncation
# Q5: How long are tool-output/ files retained? Can they be reliably associated back to session_id?
# Q6: What are the exact truncation thresholds per tool? (Currently observed: ~50 lines for glob, ~250 lines for codesearch, ~1700 tokens)
# Q7: How is custom metadata from MCPs exposed? Is there a .meta field similar to https://gofastmcp.com?

# C. File & Patch Data
# Q8: Why are there two sources for file modifications? (Part-level patch vs session-level session_diff)
# Q9: Where is the full unified diff stored? Patch only contains file list and hash.

# D. SubAgent/Session
# Q10: In tool.task's spawned_tools, the part_ids appear to be fictional/internal—can these map to actual part files?
# Q11: What's the relationship between parent_id in Session and subagent spawning?

# E. Tokens & Cost
# Q12: What do tokens_reasoning magnitudes signify? (Sometimes 0/1, sometimes 120+)
# Q13: What specifically do cache.read and cache.write tokens measure?

# F. Sandboxes & VCS
# Q14: What data should map to the sandboxes array in Project? (Daytona droplets?)
# Q15: How can we access actual git snapshot content from storage/snapshot/ for turn boundaries?

# G. Schema & Types
# Q16: Why does mode always equal agent? Is this legacy/redundant?
# Q17: Why is subagent path-form "subagent/researcher" in some places but just "name" in others?


class LogBuilderBackend:
    """Internal utility functions for loading and processing OpenCode log files from disk.

    LogBuilderBackend provides static methods that handle the low-level file I/O operations
    required to parse OpenCode's JSON storage format into Python dataclasses. These methods
    serve as the foundation for the higher-level LogBuilder API.

    Design choice: All methods are static with no instance state, allowing flexible composition
    without object instantiation. Error handling returns None or empty lists rather than
    raising exceptions, enabling partial loads to continue. Generally errors should propagate
    to the top per-session load/hydration for the system administrator, unless otherwise
    noted for specific reasons described therein.
    """

    @staticmethod
    def subprocess_run(cmd: list[str]) -> str:
        """Executes a shell command via subprocess and returns its stdout.

        Runs the provided command list using subprocess.run with check=True, capturing
        output for later processing. Errors are logged and re-raised.

        Design choice: Uses subprocess rather than native Python file operations because
        find commands are orders of magnitude faster for recursive file globbing across
        large directory trees.

        Attributes:
            cmd: list[str] - Shell command and arguments to execute
        Returns:
            str - Standard output from the command
        Raises:
            subprocess.CalledProcessError - When command returns non-zero exit code
        """
        try:
            logger.debug(f"subprocess.run cmd={cmd}:")
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return result.stdout
        except subprocess.CalledProcessError as e:
            logger.error(f"subprocess.run failed code={e.returncode} cmd={cmd} error={e.stderr}")
            raise e

    @staticmethod
    def simple_recursive_filename_matches(directory: str, pattern: str) -> list[str]:
        """Finds all files matching a recursive glob pattern within a directory.

        Executes the find command with -type f and -name flags to recursively locate
        files matching the given pattern. Returns absolute paths as a list.

        Design choice: Delegates to subprocess_run for performance. Returns empty list
        if directory doesn't exist, allowing graceful handling of missing data at the
        per-class requirement level rather than the generic os-interaction level.

        Attributes:
            directory: str - Root directory to search recursively
            pattern: str - Glob pattern (e.g., "*.json", "prt_*.json")
        Returns:
            list[str] - Absolute paths of matching files
        """
        cmd = ["find", directory, "-type", "f", "-name", pattern]
        output = LogBuilderBackend.subprocess_run(cmd)
        return [o.strip() for o in output.strip().split("\n") if len(o.strip()) != 0]

    @staticmethod
    def import_filename_into_opencode_data_model(filename: str, cls: type):
        """Parses a single JSON file into a dataclass instance.

        Loads the JSON file, then passes it to recursive_constructor along with the
        target dataclass type for field mapping and construction. This allows our
        dataclass objects to persist with more user-friendly or proprietary attribute
        names across applications without relying on OpenCode JSON structures to dictate
        our naming conventions. The recursive nature allows reconstitution of attributes
        which themselves may be dataclasses.

        Design choice: Returns None on any error rather than raising, allowing batch
        operations to continue even when individual files are corrupted. Errors are
        logged at warning level for debugging.

        Attributes:
            filename: str - Path to JSON file to parse
            cls: type - Dataclass type to construct (must have _keys mapping)
        Returns:
            Instance of cls, or None if parsing fails
        """
        try:
            with open(filename, "r") as f: data = json.load(f)
            return recursive_constructor(data, cls)
        except Exception as e:
            logger.warning(f"cls={cls} unable to parse from filename={filename} error={e}")
            return None

    @staticmethod
    def import_filename_into_list_of_opencode_data_model(filename: str, cls: type) -> list:
        """Parses a JSON array file into a list of dataclass instances.

        Loads a JSON file containing an array, then iterates through each element
        constructing a dataclass instance via recursive_constructor.

        Design choice: Returns empty list if file doesn't exist, so that optional data
        files (like todo/ or session_diff/) that may not be present for every session are
        constituted as empty iterable arrays. Each array element is processed independently;
        failures skip that item.

        Attributes:
            filename: str - Path to JSON file containing array of objects
            cls: type - Dataclass type to construct for each array element
        Returns:
            list - List of cls instances, or empty list if file missing
        """
        if not os.path.exists(filename):
            logger.debug(f"filename={filename} does not exist so no cls={cls} to load")
            return []
        try:
            items = []
            with open(filename, "r") as f: data = json.load(f)
            for item in data:
                items.append(recursive_constructor(item, cls))
            return items
        except Exception as e:
            logger.warning(f"filename={filename} unable to parse cls={cls} error={e}")
            return []

    @staticmethod
    def profile_and_filter(items, remove_value=None):
        """Filters items and logs statistics about the collection.

        Removes instances matching remove_value (typically None from failed parses),
        then logs the count of items before/after filtering and the class name.

        Design choice: Provides visibility into data quality during batch loads by
        reporting how many items were filtered. Useful for identifying corrupt or
        invalid data files, but should nearly never filter items out, after this
        version of our LogBuilder.

        Attributes:
            items: list - List of objects to filter
            remove_value: Any - Value to filter out (default None)
        Returns:
            list - Filtered items
        """
        n_items = len(items)
        items = [item for item in items if item != remove_value]
        cls_name = "unknown" if len(items) == 0 else items[0].__class__.__name__
        logger.info(f"{cls_name} is n_items={len(items)} with n_removed={n_items-len(items)}")
        return items

    @staticmethod
    def session_retrieve_todos(session: Session) -> Session:
        """Hydrates workflow todo items for a session from storage/todo/.

        Constructs the path to the session's todo JSON file and parses it into
        SessionToDo dataclass instances, attaching them to session.todos.

        Design choice: Modifies the session object in-place and returns it for
        method chaining. Returns empty list if the todo file doesn't exist, since
        not all sessions use the workflow engine. This allows the todos attribute
        to be guaranteed to be able to be iterated on without checking for None.

        Attributes:
            session: Session - Session object to attach todos to
        Returns:
            Session - The same session with todos populated
        """
        filename = os.path.join(base_dir, f"todo/{session.session_id}.json")
        session.todos = LogBuilderBackend.import_filename_into_list_of_opencode_data_model(filename, SessionToDo)
        return session

    @staticmethod
    def session_retrieve_file_diffs(session: Session) -> Session:
        """Hydrates file modification records for a session from storage/session_diff/.

        Constructs the path to the session's diff JSON file and parses it into
        FileModification dataclass instances, attaching them to session.file_diffs.

        Design choice: Modifies session in-place for method chaining. Returns empty
        list if no file modifications occurred during the session, so the attribute
        can be guaranteed to be able to be iterated on without checking for None.

        Attributes:
            session: Session - Session object to attach file_diffs to
        Returns:
            Session - The same session with file_diffs populated
        """
        filename = os.path.join(base_dir, f"session_diff/{session.session_id}.json")
        session.file_diffs = LogBuilderBackend.import_filename_into_list_of_opencode_data_model(filename, FileModification)
        return session

    @staticmethod
    def get_message_parts_filenames(message_id: str) -> list[str]:
        """Finds all part files (prt_*.json) belonging to a message.

        Searches the storage/part/{message_id}/ directory for JSON files matching
        the prt_*.json pattern.

        Design choice: Returns empty list without parts or malformed data, and is the
        only stage for which a logger message is typically recorded, as a rare subset
        of messages correctly do not contain parts because of immediate interruption
        by the user.

        Attributes:
            message_id: str - Message identifier (e.g., "msg_abc123")
        Returns:
            list[str] - Absolute paths to prt_*.json files
        """
        directory = os.path.join(base_dir, f"storage/part/{message_id}/")
        pattern = "prt_*.json"
        if not os.path.exists(directory):
            logger.info(f"directory={directory} does not exist has no parts? message={message_id}")
            return []
        return LogBuilderBackend.simple_recursive_filename_matches(directory, pattern)

    @staticmethod
    def message_retrieve_parts(message: UserMessage | AssistantMessage) -> UserMessage | AssistantMessage | None:
        """Hydrates all message parts for a message from storage/part/.

        Finds all prt_*.json files for the message, parses each into the appropriate
        OpenCodePartMessageJson subclass, then sorts them into approximate display order
        using the sort functions.

        Design choice: Returns None if any part file is missing (corrupted data). Sorts 
        parts differently for user vs assistant messages since they contain different 
        part types in different orders.

        Attributes:
            message: UserMessage | AssistantMessage - Message to attach parts to
        Returns:
            The same message with parts populated, or None if data is corrupted
        """
        filenames = LogBuilderBackend.get_message_parts_filenames(message_id=message.message_id)
        parts = []
        for filename in filenames:
            if not os.path.exists(filename):
                logger.warning(f"filename={filename} does not exist so message is None (skipped)")
                return None
            with open(filename, "r") as f:
                parts.append(hydrate_message_part_from_str(f.read()))
        if isinstance(message, AssistantMessage): parts = sort_assistant_message_parts_approximately(parts)
        elif isinstance(message, UserMessage): parts = sort_user_message_parts_approximately(parts)
        else: raise RuntimeError(f"message={message} is an unknown type and this error should not occur")
        message.parts = parts
        return message

    @staticmethod
    def get_session_message_filenames(session: Session) -> list[str]:
        """Finds all message files (msg_*.json) belonging to a session.

        Searches the storage/message/{session_id}/ directory for JSON files matching
        the msg_*.json pattern. Initializes session.messages to an empty list.

        Design choice: Initializes messages as empty list to ensure it's always a list,
        simplifying downstream code that iterates over messages.

        Attributes:
            session: Session - Session object to find messages for
        Returns:
            list[str] - Absolute paths to msg_*.json files
        """
        directory = os.path.join(base_dir, f"storage/message/{session.session_id}/")
        pattern = "msg_*.json"
        if not os.path.exists(directory):
            logger.info(f"directory={directory} does not exist so session has no messages session={session}")
            return []
        return LogBuilderBackend.simple_recursive_filename_matches(directory, pattern)

    @staticmethod
    def sessions_retrieve_messages(session: Session) -> Session:
        """Hydrates all messages for a session from storage/message/.

        Finds all msg_*.json files, determines role (user/assistant) from the JSON,
        constructs the appropriate dataclass (UserMessage or AssistantMessage),
        then hydrates each message with its parts.

        Design choice: Messages are sorted by timestamp_ms after loading. At present 
        there are only ever two role types (user/assistant) and this method should 
        throw an error back to the top when a new role is discovered, to alert the 
        system administrator. Skips messages that fail to load rather than aborting 
        the entire session.

        Attributes:
            session: Session - Session object to attach messages to
        Returns:
            Session - The same session with messages populated
        """
        filenames = LogBuilderBackend.get_session_message_filenames(session)
        for filename in filenames:
            with open(filename, "r") as f: data = json.load(f)
            if data.get("role") == "user": model = UserMessage
            elif data.get("role") == "assistant": model = AssistantMessage
            else: raise RuntimeError(f"session={session.session_id} filename={filename} data role={data.get('role')}")
            message = recursive_constructor(data, model)
            message = LogBuilderBackend.message_retrieve_parts(message)  # get strings of message parts
            if message is None: continue
            session.messages.append(message)
        session.messages = sorted(session.messages, key=lambda m: m.timestamp_ms)
        return session

    @staticmethod
    def get_project_id_from_session_id(directory: str, session_id: str) -> Project:
        """Derives the project_id from a session_id by locating its session file.

        Searches for the session file in storage/session/, extracts the parent
        directory name (which is the project_id), then loads the full Project.

        Design choice: Raises if session not found or multiple matches exist
        (should never happen). Uses file path rather than storing project_id
        in session for data integrity.

        Attributes:
            directory: str - Base OpenCode storage directory
            session_id: str - Session identifier (e.g., "ses_abc123")
        Returns:
            Project - The Project object containing this session
        Raises:
            RuntimeError - If session file not found or multiple exist
        """
        filenames = LogBuilderBackend.simple_recursive_filename_matches(os.path.join(directory, "storage/session/"), f"{session_id}.json" )
        if len(filenames) == 0: raise RuntimeError(f"session={session_id} does not exist in directory={directory}")
        if len(filenames) > 1: raise RuntimeError(f"session={session_id} has multiple files (should not occur) in directory={directory}")
        project_id = Path(os.path.dirname(filenames[0])).parts[-1]
        return LogBuilder.load_project(directory, project_id)


class LogBuilder:
    """High-level API for loading OpenCode session logs from disk into Python dataclasses.

    LogBuilder provides the primary interface for applications to load OpenCode sessions,
    projects, and their associated data (messages, parts, todos, file modifications).
    All methods return fully-populated Python dataclasses ready for use.

    Design choice: The system management of our auto-code environment starts at 
    project-level insight throughout the management of multiple sessions, and thus 
    is architected as such. Generally errors should propagate to the top per-session 
    load/hydration for the system administrator, unless otherwise noted for specific 
    reasons described therein.
    """

    @staticmethod
    def retrieve_projects() -> list[Project]:
        """Hydrates projects from the OpenCode storage/project/ directory.

        Searches for all *.json files in the project directory and constructs
        Project dataclass instances for each.

        Design choice: Returns filtered list with None/failed parses removed, logging
        statistics about project count. This is the entry point for discovering
        all known OpenCode projects.

        Returns:
            list[Project] - All discovered projects
        """
        directory = os.path.join(base_dir, "storage/project/")
        pattern = "*.json"
        filenames = LogBuilderBackend.simple_recursive_filename_matches(directory, pattern)
        projects = [LogBuilderBackend.import_filename_into_opencode_data_model(filename, Project) for filename in filenames]
        return LogBuilderBackend.profile_and_filter(projects)

    @staticmethod
    def project_retrieve_sessions(projects: list[Project], hydrate=True) -> list[Session]:
        """Hydrates sessions for the given projects from storage/session/.

        For each project, finds all ses_*.json files and constructs Session dataclasses.
        If hydrate=True, also loads todos, file_diffs, and messages and message parts 
        for each session.

        Design choice: The system management of our auto-code environment starts at 
        project-level insight throughout the management of multiple sessions, and thus 
        is architected as such. The word count of session summaries typically ranges 
        1K-5K for 85% of sessions, providing a quick sanity check during loads.

        Attributes:
            projects: list[Project] - Projects to load sessions for
            hydrate: bool - Whether to load full message/part data (default True)
        Returns:
            list[Session] - All sessions with varying hydration levels
        """
        sessions = []
        pattern = "ses_*.json"
        for project in projects:
            directory = os.path.join(base_dir, f"storage/session/{project.project_id}")
            filenames = LogBuilderBackend.simple_recursive_filename_matches(directory, pattern)
            for filename in filenames:
                start = time.perf_counter()
                session = LogBuilderBackend.import_filename_into_opencode_data_model(filename, Session)
                if session is None:
                    logger.warning(f"Session missing filename={filename}")
                    continue
                session.project = project
                if hydrate:
                    session = LogBuilderBackend.session_retrieve_todos(session)
                    session = LogBuilderBackend.session_retrieve_file_diffs(session)
                    session = LogBuilderBackend.sessions_retrieve_messages(session)
                logger.info(f"loaded {'hydrated ' if hydrate else ''}session={session.session_id} in delta={time.perf_counter() - start:.2f}s")
                # print(len("\n".join(session.as_summary_lines()).split()))  # sanity check typical summary token size
                sessions.append(session)
        return sessions

    @staticmethod
    def load_project(directory: str, project_id: str) -> Project:
        """Hydrates a single project by project_id from the given directory.

        Searches storage/project/ for the project JSON file and constructs
        the Project dataclass.

        Design choice: Most useful when loading projects and sessions from an archival 
        directory for which each directory contains precisely one project-session pair, 
        in the original OpenCode directory format so that all our native directory 
        tools work identically.

        Attributes:
            directory: str - Base OpenCode storage directory
            project_id: str - Project identifier (e.g., "60d8e4929ee0fe0a826b6df31ea8c721dfee3b80")
        Returns:
            Project - The requested project
        Raises:
            RuntimeError - If project not found or multiple exist
        """
        filenames = LogBuilderBackend.simple_recursive_filename_matches(os.path.join(directory, "storage/project/"), f"{project_id}.json" )
        if len(filenames) == 0: raise RuntimeError(f"project={project_id} does not exist in directory={directory}")
        if len(filenames) > 1: raise RuntimeError(f"project={project_id} has multiple files (should not occur) in directory={directory}")
        return LogBuilderBackend.import_filename_into_opencode_data_model(filenames[0], Project)

    @staticmethod
    def load_session(session_id: str, directory: str, hydrate=False) -> Session:
        """Hydrates a single session by session_id from the given directory.

        Finds the session file in storage/session/, derives the project_id from
        the file path, loads the parent Project, and constructs the Session dataclass.
        If hydrate=True, also loads todos, file_diffs, and messages.

        Design choice: Project is always loaded to provide context. The directory
        parameter enables loading from non-degault locations (e.g., migrated backups, 
        see note in load_project()).

        Attributes:
            session_id: str - Session identifier (e.g., "ses_3ac09ec82ffeEgy0wXJS0EcEEx")
            directory: str - Base OpenCode storage directory
            hydrate: bool - Whether to load full message/part data (default False)
        Returns:
            Session - The requested session with project reference
        Raises:
            RuntimeError - If session not found or multiple exist
        """
        start = time.perf_counter()
        filenames = LogBuilderBackend.simple_recursive_filename_matches(os.path.join(directory, "storage/session/"), f"{session_id}.json" )
        if len(filenames) == 0: raise RuntimeError(f"session={session_id} does not exist in directory={directory}")
        if len(filenames) > 1: raise RuntimeError(f"session={session_id} has multiple files (should not occur) in directory={directory}")
        session = LogBuilderBackend.import_filename_into_opencode_data_model(filenames[0], Session)
        session.project = LogBuilderBackend.get_project_id_from_session_id(directory, session_id)
        if session is not None and hydrate:
            session = LogBuilderBackend.session_retrieve_todos(session)
            session = LogBuilderBackend.session_retrieve_file_diffs(session)
            session = LogBuilderBackend.sessions_retrieve_messages(session)
        logger.info(f"loaded {'hydrated ' if hydrate else ''}session={session_id} in delta={time.perf_counter() - start:.2f}s")
        return session

class LogMigrator:
    """Utilities for reorganizing and backing up OpenCode session logs to new directory structures.

    LogMigrator provides methods to copy session logs from the standard OpenCode storage location
    into specifically a reorganized directory structure, enabling one-to-one archiving of a session
    to a new directory with internal structures directly matching the standard OpenCode storage location,
    but only containing that one project-session pair. This is an important development to atomic archiving.

    Design choice: The standard OpenCode storage location is ~/.local/share/opencode/ with various subdirectories,
    most notably storage/ (which holds the majority of per-session log content, messages, etc.) and tool-output/.
    Each migrated session gets its own exactly identical directory under {new_base_dir}/{project_id}/{session_id}/
    preserving the original storage/ subdirectory layout for compatibility with LogBuilder. At this time tool-output/
    is not preserved as we do not know what the proper handling of this data is for persistence in the OpenCode use.
    TODO: other subdirectories.
    """

    @staticmethod
    def internal_directory_of_session(session: Session, new_base_dir: str) -> str:
        return os.path.join(new_base_dir, session.project.project_id, session.session_id)

    @staticmethod
    def migrate_one_session_log_to_new_directory(session: Session, original_log_dir: str, new_base_dir: str) -> str:
        """Copies all files associated with a project-session to its project-session pair archived directory structure.

        Reorganizes {all_original_structure} logs into {new_base_dir}/{project_id}/{session_id}/
        preserving i.e., the storage/ directory layout but isolating each session and its project at the time of
        archiving. This enables archival backup and continued analysis of individual sessions with these same tools.

        Design choice: See above. Creates necessary directories as needed. Silently skips missing optional
        files (todo, session_diff) rather than failing, since not all sessions have them.

        Attributes:
            session: Session - Session to migrate (must be hydrated)
            original_log_dir: str - Source directory containing original storage/
            new_base_dir: str - Destination root for reorganized structure
        """
        # the new_base_dir has within it {project_id}/{session_id}/{original_structure} structure to make readability. In
        # other words, inside new_base_dir will be all the project_ids, inside of which is the session_id folder for each
        # session of the project, and inside the session_id folder will be the original project/ session/ todo/ message/
        # parts/ etc. structure containing information for only one session across each of the directories, so that
        # loading "all" sessions from new_base_dir/{project_id}/{session_id}/ using standard tools yields one complete result
        to_directory = LogMigrator.internal_directory_of_session(session, new_base_dir)

        def do_copy(_stub: str, no_warn_on_dne=False):
            _from_filename = os.path.join(original_log_dir, _stub)
            _to_filename = os.path.join(to_directory, _stub)
            os.makedirs(os.path.dirname(_to_filename), exist_ok=True)
            if os.path.exists(_from_filename):
                shutil.copy2(_from_filename, _to_filename)
            else:
                if not no_warn_on_dne: logger.warning(f"stub={_stub} not found in {original_log_dir}")

        # copy the project file to base_dir/project/{project_id}.json
        # copy the session file to base_dir/session/
        do_copy(f"storage/project/{session.project.project_id}.json")
        do_copy(f"storage/session/{session.project.project_id}/{session.session_id}.json")
        do_copy(f"storage/todo/{session.session_id}.json", no_warn_on_dne=True)
        do_copy(f"storage/session_diff/{session.session_id}.json", no_warn_on_dne=True)
        filenames = LogBuilderBackend.get_session_message_filenames(session)
        for filename in filenames:
            stub = os.path.relpath(filename, original_log_dir)
            do_copy(stub)
            message_id = Path(os.path.basename(filename)).stem
            parts = LogBuilderBackend.get_message_parts_filenames(message_id=message_id)
            for part in parts:
                stub = os.path.relpath(part, original_log_dir)
                do_copy(stub)
        return to_directory


# INSIDE storage/
# project <== contains project definitions, i.e., root directories opencode has been run in
# session <== one session_id, as {project_id}/{session_id}.json is created for each new opening of opencode or session
# migration <== a file, containing the character "2", presumable for internal logging purposes of version changes?
# todo <== IF a session creates itself worklist TODO set, this contains its tasks, and its status; very important to OS
# message <== message metadatq of both role=user and role=assistant
# part <== specific message (i.e., tool call/response, or text output, or path, or turn start, of data etc.)
# session_diff <== accounting of each file content change from start of session to end of session, including content.

# INSIDE tool-calls/
# Note: these are indexed by tool, and presumably created for reference inside their use session, rather than the
# truncated information in the message.part .tool, and deleted immediately for security reasons. But in my exploration,
# instead what I found is that these files persist for long periods of times (many days, dozens of sessions, etc.)

# session.as_summary_lines() has been between 1K and 5K .split() length for 85% of sessions; 10K-30K for 10% top. Good!


# SAMPLE USAGE
base_dir = "~/.local/share/opencode/"
base_dir = os.path.expanduser(base_dir)
to_dir = "~/.local/share/starlight/codeo/logs/"
to_dir = os.path.expanduser(to_dir)

# # Example: transfer a specific session_id logs to a new copy-share directory in {to_dir}/{project_id}/{session_id}/
# # This allows the user to copy/move that directory entirely and only possess the contents of session_id
#
# # load the session, migrate the session to to_dir; then, to sanity check we load the moved session and check its diffs
# # pip install deepdiff; is very useful for identifying dict-dict difference, which should be an empty dict in our case
# session_id = "ses_3fb06a35effezpB9vzIIGfR0Nf"
# session = LogBuilder.load_session(session_id, base_dir, hydrate=True)
# LogMigrator.migrate_one_session_log_to_new_directory(session, original_log_dir=base_dir, new_base_dir=to_dir)
#
# from deepdiff import DeepDiff
# to_directory = LogMigrator.internal_directory_of_session(session, to_dir)
# session_moved = LogBuilder.load_session(session_id, to_directory, hydrate=True)
#
# diff = DeepDiff(dataclasses.asdict(session), dataclasses.asdict(session_moved), ignore_order=True) # ignore_order is useful for JSON
# print("this should be empty if the copy is exact", json.dumps(diff, indent=4))  # should be empty

# Example: load and hydrate all session_ids

# # get all projects (i.e., root directories of sessions), then get all the sessions of all the project.
# # this hydrates at about 10 sessions/second, so could easily take a minute or two to compile all of the sessions
# projects = LogBuilder.retrieve_projects()
# sessions = LogBuilder.project_retrieve_sessions(projects, hydrate=False)


# Example: migrate all session_ids, but also write their readable logs to {to_dir} to parse as persistence memory etc.
# without hydration, loading the sessions is almost instantaneous; we will transfer & hydrate & write one by one
# projects = LogBuilder.retrieve_projects()
# sessions = LogBuilder.project_retrieve_sessions(projects, hydrate=False)
# for session in sessions:
#     to_directory = LogMigrator.internal_directory_of_session(session, to_dir)
#     if os.path.exists(to_directory): continue  # only copy directories we have not yet copied.
#     LogMigrator.migrate_one_session_log_to_new_directory(session, original_log_dir=base_dir, new_base_dir=to_dir)
#     session_moved = LogBuilder.load_session(session.session_id, to_directory, hydrate=True)
#     as_readable = session_moved.as_summary_lines()
#     print(f"{session.session_id} has n_lines={len(as_readable)}")
#     with open(os.path.join(to_dir, f"readable.codeo.{session.session_id}.txt"), "w") as f: f.write("".join(as_readable))


