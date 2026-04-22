from typing import Any, TypedDict
import dataclasses
import json
import re

##########################################################################################
# Added standardized OpenCode --format json output types with parsers and easy summaries
##########################################################################################


def simple_wrangler(data: dict, keys: str, not_found=None) -> Any:
    """Accesses nested dictionary values using dot-notation keys for readable code.
    
    Traverses a dictionary by splitting the keys parameter on dots (e.g., "a.b.c"),
    recursively accessing each level. Returns not_found if any key is missing or 
    traversal fails.
    
    Design choice: This utility makes simpler the accessing of deeper nested attributes
    in OpenCode's raw JSON by specifying their location with a simple dot-notation string,
    which maximizes readability and robustness in the per-class definitions.
    """
    key_list = [k.strip() for k in keys.split(".") if len(k.strip()) != 0]
    if len(key_list) == 0:
        return not_found
    if key_list[0] not in data:
        return not_found
    value = data[key_list[0]]
    return value if len(key_list) == 1 else simple_wrangler(value, ".".join(key_list[1:]), not_found=not_found)
def simple_wrangle_for_many(data: dict, to_wrangle: dict[str, str], not_found=None) -> dict[str, Any]:
    """Applies simple_wrangler to multiple keys in a single call.
    
    Takes a mapping of desired output keys to their dot-notation source paths (e.g.,
    {"field_a": "state.input.a", "field_b": "state.input.b"}) and returns a dict
    with the retrieved values.
    
    Design choice: This utility exists to support the _keys class attribute pattern,
    where each dataclass defines a mapping from "our attribute names" to "their
    dot-notation JSON paths" for bulk transformation before construction.
    """
    if data is None: return not_found
    return {k: simple_wrangler(data, v, not_found=not_found) for k, v in to_wrangle.items()}

def recursive_constructor(data: dict, cls) -> Any:
    """Recursively constructs a dataclass instance from a dictionary, handling nested types.
    
    First applies the cls._keys mapping (if present) to transform raw JSON field names to
    our attribute names. Then recursively processes each field: if the field type is itself
    a dataclass, it is recursively constructed; if it is a list/tuple of dataclasses,
    each element is constructed; if it is a dict of dataclasses, each value is constructed.
    
    Design choice: This handles the construction of our dataclasses in Python, where attributes 
    may be dataclasses themselves, and performs the _keys transformation at each level of 
    construction because its data arises from OpenCode's deeply nested JSON structure where 
    values may constitute reasonable high-level dataclasses on our side to maintain data 
    integrity and ease of use.
    """
    # NOTE: unfortunately, since the _keys map dict field names to new attribute names, we need to call those internal
    # to this function, which makes this not a universal function for dataclasses, as we have elsewhere in our packages
    # if you want the generic version just go ahead and comment out the two lines below that make sense to do so
    if hasattr(cls, "_keys"): data = simple_wrangle_for_many(data, cls._keys)
    item = cls(**data)
    for field in dataclasses.fields(cls):
        origin = getattr(field.type, '__origin__', None)
        if origin is None and dataclasses.is_dataclass(field.type) :
            raw = getattr(item, field.name)
            if raw is None: raw = dict()  # empty dict of dataclass values
            if hasattr(field.type, "_keys"): raw = simple_wrangle_for_many(raw, field.type._keys)
            if raw is not None:
                setattr(item, field.name, field.type(**raw))  # recompile into dataclass
        if origin in [list, tuple]:
            underlying_cls = getattr(field.type, '__args__')[0]
            raw = getattr(item, field.name)
            if raw is None: raw = []  # empty list of dicts of dataclass values
            if dataclasses.is_dataclass(underlying_cls) and raw is not None:
                raw = [simple_wrangle_for_many(v, underlying_cls._keys) if hasattr(underlying_cls, "_keys") else v for v in raw]
                values = [recursive_constructor(v, underlying_cls) for v in raw]
                setattr(item, field.name, values)
        if origin in [dict]:
            underlying_cls = getattr(field.type, '__args__')[1]
            raw = getattr(item, field.name)
            if raw is None: raw = dict()  # empty dict of dicts of dataclass values
            if dataclasses.is_dataclass(underlying_cls) and raw is not None:
                raw = {k: simple_wrangle_for_many(v, underlying_cls._keys) if hasattr(underlying_cls, "_keys") else v for k, v in raw.items()}
                values = {k: recursive_constructor(v, underlying_cls) for k, v in raw}
                setattr(item, field.name, values)
    return item

##########################################################################################
# The following are dataclasses for PARTS data, which are both returned by the REST API
# and for TUI sessions are stored in the part/{message_id}/ directory as prt_*.json files.
##########################################################################################

# NOTE: not all fields are handled using the classes below, and their original primary intention
# was the construction of tightly useful summary of each part for display and summarization.
# It is welcomed to expand upon these, and particularly useful to set up a specific REPL lookup.

@dataclasses.dataclass
class OpenCodePartMessageJson:
    """Base class for all message part types in OpenCode's JSON output.
    
    Each part represents a discrete component of a message: text, tool invocations,
    reasoning traces, file patches, or references. This base class provides common
    fields (part_id, session_id, message_id) and the _keys mapping from our attribute
    names to their dot-notation JSON paths.
    
    Design choice: The _keys mapping is defined at class level to transform raw JSON
    field names before dataclass construction. Subclasses override _oc_name to match
    OpenCode's type strings. Hooks allow extending summary output per-instance.
    
    Attributes:
        part_id: str - Unique identifier for this part (their path: id, e.g., "prt_c067c6b04002np2GC93CFiQCqf")
        session_id: str - session_id containing this part (their path: sessionID, e.g., "ses_3f9a0c857ffeQV5bsrY34Qnq21")
        message_id: str - message_id containing this part (their path: messageID, e.g., "msg_c067c5e73001E2HgIdArAgeWfm")
        _name: str - Our display name used in printed summaries (e.g., "text", "tool_use")
        _oc_name: str | None - Matches OpenCode's raw JSON "type" field (e.g., "text", "tool")
        _hooks_for_additional_summary_lines: list[Callable[[OpenCodePartMessageJson, list[str]], list[str]]] -
            Optional callable hooks for post-processing our summary lines with additional or derived 
            information from self, or to support third-party part types (e.g., a Tool part that is 
            checked to be a specific third-party MCP with custom summaries added)
        _keys: dict[str, str] - Maps our attribute names to their raw JSON paths in OpenCode's output
    """
    part_id: str
    session_id: str
    message_id: str
    _name = "generic"
    _oc_name = None  # override in inherited class
    _hooks_for_additional_summary_lines = []  # list[Callable[[], list[str]]]
    _keys = dict(session_id="sessionID", message_id="messageID", part_id="id")  # extend these inherited class

    @classmethod
    def handle_from_part_data(cls, data: dict) -> "OpenCodePartMessageJson":
        # parsed = {parameter: simple_wrangler(data, accessor) for parameter, accessor in cls._keys.items()}
        # forgotten = [k for k in data.keys() if k not in cls._keys.values()]
        # forgotten = [k for k in forgotten if not any([v.startswith(f"{k}.") for v in cls._keys.values()])]
        # forgotten = [k for k in forgotten if k not in ["type"]]
        # if len(forgotten) > 0: print(cls.__name__, forgotten)
        return recursive_constructor(data, cls)

    def as_summary_lines(self) -> list[str]:
        """Generates a human-readable summary of this message part as a list of strings.
        This is the base implementation that returns a placeholder message indicating no additional content.
        Subclasses override this method to provide specific summaries including part identifiers, timestamps,
        and content-specific information. The format is "{self._name}: <field>=<value>" enabling
        grep-friendly log parsing across all part types.
        Example output:
            generic: no additional content provided"""
        return [f"{self._name}: no additional content provided"]  # overwrite in inherited class

    def extend_by_hooks(self, lines):
        if self._hooks_for_additional_summary_lines is not None:
            [lines.extend(hook(self)) for hook in self._hooks_for_additional_summary_lines]
        return lines


@dataclasses.dataclass
class OpenCodeText(OpenCodePartMessageJson):
    """A text part containing user or assistant message content.
    
    Represents a text segment within a message, typically the main body of a user prompt
    or assistant response. Includes timing information to determine execution order relative
    to other parts.
    
    Design choice: Text parts are sorted by their timestamp when available, as they may appear
    before, after, or interleaved with reasoning and tool calls.
    
    Attributes:
        time_start_ms: int - Unix timestamp when text started (their path: time.start, e.g., 1769635346078)
        time_end_ms: int - Unix timestamp when text ended (their path: time.end, e.g., 1769635346078)
        text: str - The actual text content (their path: text, e.g., "Plan complete and saved to...")
    
    Inherited from OpenCodePartMessageJson:
        part_id: str - Unique identifier for this part (their path: id, e.g., "prt_c067c6b04002np2GC93CFiQCqf")
        session_id: str - session_id containing this part (their path: sessionID, e.g., "ses_3f9a0c857ffeQV5bsrY34Qnq21")
        message_id: str - message_id containing this part (their path: messageID, e.g., "msg_c067c5e73001E2HgIdArAgeWfm")
        _name: str - Our display name used in printed summaries
        _oc_name: str | None - Matches OpenCode's raw JSON "type" field
        _hooks_for_additional_summary_lines: list - Hooks for post-processing summary lines
        _keys: dict - Maps our attributes to their JSON paths
    """
    time_start_ms: int
    time_end_ms: int
    text: str
    _name = "text"
    _oc_name = "text"
    _keys = OpenCodePartMessageJson._keys | dict(time_start_ms="time.start", time_end_ms="time.end", text="text")

    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this text part as a list of strings for logging and debugging.
        Includes the part_id, timestamps (time_start_ms and time_end_ms) for sorting and timing analysis,
        and the full text content demarcated with START/END markers to clearly delimit the message body.
        The format uses the pattern "{self._name}: <field>=<value>" enabling grep-friendly log parsing
        while the demarcation markers allow easy extraction of the actual text content from the summary.
        Example output:
            text: part_id=prt_c067c6b04002np2GC93CFiQCqf time_start_ms=1769635346078 time_end_ms=1769635346078
            text: ----- DEMARCATION OF TEXT_START -----
            text: Plan complete and saved to /path/to/plan.md
            text: ----- DEMARCATION OF TEXT_END -----"""
        return self.extend_by_hooks([
            f"{self._name}: part_id={self.part_id} time_start_ms={self.time_start_ms} time_end_ms={self.time_end_ms}",
            f"{self._name}: ----- DEMARCATION OF TEXT_START -----",
            f"{self._name}: {self.text}",
            f"{self._name}: ----- DEMARCATION OF TEXT_END -----",
        ])


class MetadataToolTodowrite(TypedDict):
    content: str   # "Phase 1: Conduct comprehensive research on {subject}"
    id: str        # "1"
    priority: str  # "high" presumably an enum
    status: str    # "pending" or "complete" presumably an enum


@dataclasses.dataclass
class OpenCodeTool(OpenCodePartMessageJson):
    """A tool invocation part representing a tool call and its result during the assistant's turn.
    
    Represents a tool that was invoked by the assistant, including timing, inputs, outputs,
    and various tool-specific metadata. The output_md contains either a verbatim subsection of
    that call response, which may be truncated. This class is designed to be extensible in two
    primary ways: inheriting OpenCodeTool to create a specific instance of a tool call (i.e.,
    OpenCodeToolTask below, or one for an MCP tool often used in a proprietary stack) and by
    extending the poorly documented .extend_by_hooks() and ._print_third_party attributes to
    provide additional information about the tool being invoked from its base summary lines.

    Design choice: Tools are sorted by their timestamp when available. The class includes
    tool-specific optional fields (todowrite_todos, edit_diff, read_preview, bash_exitcode,
    skill_directory) that are extracted because they are important enough to have as first-class
    attributes but not numerous enough to warrant separate subclasses (except for tool.task).
    
    Attributes:
        part_id: str - Unique identifier for this part
            (their path: id, e.g., "prt_c5801e69f001mx5vPLab61F0Wr")
        session_id: str - session_id containing this part
            (their path: sessionID, e.g., "ses_3a83c956affeIIPCR56Ai241BX")
        message_id: str - message_id containing this part
            (their path: messageID, e.g., "msg_c5801d477001rbIhcGOv9jE3uy")
        time_start_ms: int - Unix timestamp when tool started
            (their path: state.time.start, e.g., 1771003046051)
        time_end_ms: int - Unix timestamp when tool ended
            (their path: state.time.end, e.g., 1771003048500)
        call_id: str - Unique identifier for this tool call
            (their path: callID, e.g., "call_function_mnajehi1694n_1")
        status: str - Status of the tool call
            (their path: state.status, e.g., "completed")
        name: str - Name of the tool
            (their path: tool, e.g., "bash", "read", "edit", "task")
        summary: str - Short summary/title of the tool call
            (their path: state.title, e.g., "Test Python scripts")
        inputs: dict - Input parameters passed to the tool
            (their path: state.input, e.g., {"command": "cd ...", "description": "Test"})
        output_md: str - Output of the tool as markdown, which may be truncated
            (their path: state.output, e.g., "Fetching repositories for user...")
        is_truncated: bool - Whether output was truncated
            (their path: state.metadata.truncated, e.g., true)
        output_filename: str | None - Path to full output file if truncated
            (their path: state.metadata.outputPath, e.g., "/path/to/.local/share/opencode/tool-output/tool_abc123...")
        attachments: list[dict] | None - Files returned by the tool
            (their path: state.attachments, e.g., [{"id": "file_123", "url": "data:text/plain;base64,..."}])
        error: str | None - Error message if tool failed
            (their path: state.error, e.g., "Tool execution aborted")

    Tool-Specific Attributes extracted when their respective OpenCode tools are run, i.e., usually None unless:
        todowrite_todos: MetadataToolTodowrite | None - Todos created by tool.todowrite
            (their path: state.metadata.todos, e.g., [{"id": "1", "content": "Get repositories...", "priority": "high", "status": "completed"}])
        edit_diff: str | None - Diff of file edited by tool.edit
            (their path: state.metadata.diff and contains the actual .patch file diff content text)
        read_preview: str | None - Preview string of file content read by tool.read
            (their path: state.metadata.preview, e.g., "    # }\n    # }%\n\n@dataclasses.dataclass...")
        bash_exitcode: int | None - Exit code of tool.bash
            (their path: state.metadata.exit, e.g., 0)
        skill_directory: str | None - Directory of skill loaded by tool.skill
            (their path: state.metadata.dir)
    
    Inherited from OpenCodePartMessageJson:
        (see OpenCodePartMessageJson)
    """
    # Output truncation: When tool output exceeds size limits, .is_truncated=True and .output_filename
    # points to the full content in tool-output/. The .output_md field contains either verbatim content
    # (if under limit) or an LLM-generated summary with truncation notice. See example below.
    # Example truncation message appended to output_md when truncated:
    #   ----- 14644 bytes truncated -----
    #   The tool call succeeded but the output was truncated. Full output saved to: ...tool-output/tool_c04fa8e70001uvIeC6kZI6oH5F
    #   Use the Task tool to have explore agent process this file with Grep and Read (with offset/limit).
    time_start_ms: int
    time_end_ms: int
    call_id: str
    status: str
    name: str
    summary: str
    inputs: dict[str, Any]
    output_md: str  # output str of .md content, including truncated content and filesystem to full output result.
    is_truncated: bool
    output_filename: str | None  # optional filename in tool-output/ that very likely exists if is_truncated
    attachments: list[dict] | None  # some tools return this, most likely you want .id and .url (base64 encoded uri)
    error: str | None  # an error string if the tool failed

    todowrite_todos: MetadataToolTodowrite | None
    edit_diff: str | None
    read_preview: str | None
    bash_exitcode: int | None
    skill_directory: str | None

    _print_always = ["edit", "task", "write", "question"]  # tools whose output is ALWAYS included in summary
    _print_verbose = ["glob", "websearch", "codesearch", "webfetch", "grep"]  # tools included when _use_verbose=True
    _print_not_included = ["bash", "todowrite", "skill", "read", "invalid"]  # Explicitly NOT included in output summaries

    _use_verbose = False
    _print_third_party = []  # <== provide tool names here to include as third-party (output not shown by default)
    _name = "tool_use"
    _oc_name = "tool_use"
    _keys = OpenCodePartMessageJson._keys | dict(
        name="tool",
        call_id="callID",
        time_start_ms="state.time.start",
        time_end_ms="state.time.end",  # will be None if status is not "completed" for any reason i.e. crash
        status="state.status",
        inputs="state.input",
        output_md="state.output",
        summary="state.title",
        is_truncated="state.metadata.truncated",
        attachments="state.attachments",  # some tools return list of attachments; you want .url base64 encoded
        output_filename="state.metadata.outputPath",  # webfetch, invalid, bash save content in tool-output/tool_* file
        error="state.error",
        # NOTE: it may be that other tools save output_filename, generally, when .output_md is too long, and at least
        # a few third-party MCPs make use of output_filename for the retrieval of information beyond .output.
        # below are tool.{name} specific fields which are extracted (or otherwise None) because they are important
        # enough to extract, but not so numerous that creating a subclass of OpenCodeTool is worthwhile to create.
        # We do create a OpenCodeToolTask subclass because those spawn entirely new sessions, i.e., core workflow.
        # NOTES: tool.edit we do not keep .metadata.filediff because most of this data is contained in .inputs
        # NOTES: tool.write .metadata.filepath is redundant with .inputs, but .metadata.exists we do discard here
        # NOTES: tool.glob contains .metadata.count which is discarded; state.output can be truncated at ~50 lines
        # and is reported as such, although no tool-output file is provided, just .output says "results truncated..."
        # NOTES: tool.websearch contains machine-readable .output but .metadata just has .truncated
        # NOTES: tool.codesearch is similar, truncated to ~ 250 lines and ~1700 tokens
        # NOTES: tool.webfetch is similar, with only .truncated in .metadata
        # NOTES: tool.grep contains .metadata.matches but this is contained in its .output at the top line
        # NOTES: tool.skill contains the .metadata.dir but its .name is in inputs
        # NOTES: tool.question has its summarized answer (including response) in .outputs and should be printed.
        # NOTES: MCPs are not presently handled in a special way, although return as tool.{mcp_tool_name} w/ metadata
        # It is not clear to me how custom metadata is created (this is not .meta of https://gofastmcp.com/servers/tools)
        todowrite_todos="state.metadata.todos",  # todowrite .content .id .priority .status of a new _todo list created
        edit_diff="state.metadata.diff",  # edit the specific diff of the file (probably what you want to diagnose)
        read_preview="state.metadata.preview",  # read a ~20 line head of what is read useful for preview printing
        bash_exitcode="state.metadata.exit",  # bash .output and .description are in .output and .title respectively.
        skill_directory="state.metadata.dir",  # skill .name is redundant with .inputs
    )

    # Our internal team argued extensively about what to include in tool summaries. The following is a simple
    # self evaluation for "what to summarize the tool using" suitable for software engineering retrospection.
    # The output is mechanical, and therefore easy for the LLM to read, but only partially suitable for
    # other tasks, i.e., research. For instance, research agents may benefit more from, for instance, only the text exchange
    # between the User & Assistant, with the auxiliary filenames available for any introspection agent to read on request
    # such as via an MCP. These are __crucial__ tools for OpenCode to be providing us in terns of extremely clear logs
    # documentation, and their own personal recommending "cookbooks" as this document provides. Honestly, us at StarlightHQ
    # "are a little offended" we had to do this ourselves and would expect serious enterprises to immediately format their
    # documentation so their own [OpenCode] Plan agent could generate the entirety of this StarlightHQ cookbook on demand,
    # from a simple prompt, to create application-specific summarization tools. This is day one use cases inside a startup,
    # and its lack of transparency greatly diminishes the value of OpenCode as a vehicle for investment "in my opinion". As
    # I said, OpenCode is invited to hire StarlightHQ so that we do not have to give away our IP as we are doing here. xo.
    # There is absolutely no reason to believe OpenCode engineers did not raise this issue internally more than a year ago.
    # PS: expect all updates to include potential breaking changes; please do pull requests if editing your own version internally.

    # February 2026.
    # _print_always = ["edit", "task", "write", "question"]  # tools whose output is ALWAYS included
    # _print_verbose = ["glob", "websearch", "codesearch", "webfetch", "grep"]  # included when _use_verbose=True
    # _print_not_included = ["bash", "todowrite", "skill", "read", "invalid"]  # Explicitly NOT included
    # _print_third_party = []  # provide tool names here to include as third-party (output not shown by default)

    # edit <== a simple note i.e., "edit done successfully"
    # read <== direct read of files in machine language; use .metadata.preview + input.filePath
    # todowrite <== .output is simply a json string of .metadata.content so use the .content data directly as we provide
    # task <== .output is the executive summary of the entire subaagent task and should be included in its entirety.
    # write <== like edit .output is a simple note of criteria i.e., "wrote file successfully"
    # glob <== a truncated output of filenames, which should be used if your application provides a need, for verbosity
    # websearch <== machine readable long-form page outputs probably better to summarize beforehand than use in summaries
    # codesearch <== more or less than same as websearch, though not always software code, to my surprise
    # webfetch <== the entirety [truncated] of the webpage fetched, probably better to summarize beforehand
    # bash <== tend to be commands with minimal output and .title suffices to explain its function, not included re: privacy
    # grep <== the truncated output of the grep tool providing filenames, probably only useful for sys admin actionability
    # skill <== output is the SKILL.md file read itself, probably unnecessary in most operating cases, use skill.directory
    # error <== the output is more or less exactly input.error and should be printed from there as an input instead
    # question <== .output will only exist if use did not dismiss, and will contain question and selected/written answer

    def as_summary_lines(self) -> list[str]:
        """Generates a comprehensive summary of this tool invocation as a list of strings for debugging and logging.
        Captures tool name, status, duration, call_id, summary, inputs, outputs, truncation status, and specialty
        outputs (skill_directory, bash_exitcode, read_preview, edit_diff, todowrite_todos). Output content is only
        included for tools in _print_always (edit, task, write, question) or _print_third_party, while other tools
        show only metadata. The format uses "{self._name}: <field>=<value>" enabling grep-friendly log analysis.
        Example output:
            tool_use: name=tool.bash status=completed duration_s=0.45
            tool_use: call_id=call_function_mnajehi1694_1 summary=List project files
            tool_use: tool_inputs:
            tool_use:   command => ls -la
            tool_use:   description => List all files in project directory
            tool_use: tool_outputs:
            tool_use:   output => {there is actually no output for bash because it is skipped but otherwise here}
            tool_use:   exitcode=0
            tool_use: is_truncated=False additional_content: None"""

        if self.time_end_ms is not None:
            duration_s = (self.time_end_ms - self.time_start_ms)/1000
            duration_str = f" duration_s={duration_s:.2f}"
        else: duration_str = ""
        inputs_strs = [f"{self._name}:   {k} => {v}" for k, v in self.inputs.items()]
        error_strs = [] if self.error is None else [f"{self._name}: error={self.error}"]
        # output the first 100 characters of attachment.url which is either a URL or base64 uri (i.e., shows mime, etc)
        attachments_strs = [] if self.attachments is None \
            else [f"{self._name}: attachment={a.get('url', '')[:100]}" for a in self.attachments]
        # see notes above.
        output_strs = []
        print_output_for_names = self._print_always + self._print_third_party
        if self._use_verbose: print_output_for_names.extend(self._print_verbose)
        if self.output_md is not None and self.name in print_output_for_names:
            output_strs = [
                f"{self._name}: ----- DEMARCATION OF OUTPUT_START -----",
                f"{self._name}:   output => {self.output_md}"
                f"{self._name}: ----- DEMARCATION OF OUTPUT_END -----",
            ]
        # specialty tools' outputs; should never be more than one at once; with extra spaces to show as indended outputs
        extras = []
        if self.skill_directory: extras.append(f"{self._name}:   skill={self.skill_directory}")
        if self.bash_exitcode: extras.append(f"{self._name}:   exitcode={self.bash_exitcode}")
        if self.read_preview: extras.append(f"{self._name}:   preview={self.read_preview}")
        if self.edit_diff: extras.append(f"{self._name}:   diff={self.edit_diff}")
        if self.todowrite_todos:
            for td in self.todowrite_todos:
                _id, _priority, _status, _content = td.get("id"), td.get("priority"), td.get("status"), td.get("content")
                extras.append(f"{self._name}: id={_id} priority={_priority} status={_status} content={_content}")
        return self.extend_by_hooks([
            f"{self._name}: name=tool.{self.name} status={self.status}{duration_str}",
            f"{self._name}: call_id={self.call_id} summary={self.summary}",
            f"{self._name}: tool_inputs:",
            *inputs_strs,
            *error_strs,
            f"{self._name}: tool_outputs: ",
            *attachments_strs,
            *extras,
            f"{self._name}: is_truncated={self.is_truncated} additional_content: {self.output_filename}",
            *output_strs,
        ])

@dataclasses.dataclass
class OpenCodeToolTask(OpenCodeTool):
    """A specialized tool invocation for the OpenCodeTool name="task", which is what spawns subagent sessions.
    
    Represents an OpenCode tool.task call that spawns a new subagent session. Includes metadata about
    the spawned session (session_id, model, provider) and a summary of all tools called
    within that subsession. This, and its .output_md provide a useful summary without requiring the
    loading of the spawned session_id itself, thereby keeping our summaries silo'd for each session_id,
    including when a session contains links to its own child sessions (i.e., subagents were spawned).
    
    Design choice: This is a specialized subclass of OpenCodeTool because tool.task has unique
    metadata (spawned_session_id, spawned_model, etc.) that warrant first-class attributes.
    The spawned_tools list provides a summary of subagent activity without requiring the
    parent agent to open the full subsession.
    
    Attributes:
        spawned_session_id: str - session_id of the spawned subagent
            (their path: state.metadata.sessionId, e.g., "ses_3b8c32c7fffe8RCNXS1oYRLLIA")
        spawned_model: str - Model used by the subagent
            (their path: state.metadata.model.modelID, e.g., "big-pickle")
        spawned_provider: str - Provider of the subagent model
            (their path: state.metadata.model.providerID, e.g., "opencode")
        spawned_tools: list[dict] - Summary of tools called in subsession
            (their path: state.metadata.summary, e.g.,
                [{"id": "prt_c473ce0ad0015xjJCt84SBBRJ1", "tool": "websearch", "state": {"status": "completed", "title": "Web search: Pennsbury..."}},
                 {"id": "prt_c473d5001001TCxGm2NhTszirq", "tool": "context7_resolve-library-id", "state": {"status": "completed", "title": ""}}])
    
    Inherited from OpenCodeTool:
        (see OpenCodeTool)
    """
    # TOOL.TASK has specific .metadata that we want to specifically extract, rather than generically, including the
    # spawned session_id (finally! subagent session_id concurrent logging!) and what appears to be a summary of all
    #  the tools called within, which is useful in a pinch (or totally) when opening subsessions is too much info.
    # in particular .metadata.summary: list[dict] with dict => part_id at .id, tool name at .tool the status at
    # .state.status the summary (one line only, i.e., "web search: etc. etc. etc.") at state.title. These are the little
    # messages shown to a primary agent TUI as the subagent mashes away on its entire session at spawned_session_id.
    # Note that the "prt_*" ids appear to be entirely fiction, and none of them appear in my logs anyway February 2025.
    spawned_session_id: str
    spawned_model: str
    spawned_provider: str
    spawned_tools: list[dict]
    _name = "tool_use.task"
    _oc_name = "tool.task"
    _keys = OpenCodeTool._keys | dict(spawned_session_id="state.metadata.sessionId",
                                      spawned_model="state.metadata.model.modelID",
                                      spawned_provider="state.metadata.model.providerID",
                                      spawned_tools="state.metadata.summary",)


    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this subagent task invocation as a list of strings, including both base tool info and subagent details.
        First calls the parent OpenCodeTool.as_summary_lines() to capture tool name, status, duration, call_id, summary, inputs, and outputs.
        Then appends the spawned_session_id, model (provider/model format), and iterates through spawned_tools to list each tool
        called within the subagent session with its name, status, part_id, and summary. This enables tracking of nested agent
        activity without loading the full subagent session. The format uses "{self._name}: <field>=<value>" for grep-friendly parsing.
        Example output:
            tool_use: name=tool.task status=completed duration_s=12.34
            tool_use: call_id=call_function_mnajehi1694_1 summary=Research Python libraries
            tool_use: tool_inputs:
            tool_use:   query => What is Context7 MCP?
            tool_use: tool_outputs:
            tool_use:   is_truncated=False additional_content: None
            tool_use.task: spawned_session_id=ses_3b8c32c7fffe8RCNXS1oYRLLIA model=opencode/big-pickle
            tool_use.task: call_id=call_function_mnajehi1694_1 summary=Research Python libraries
            tool_use.task: spawned_tools:
            tool_use.task: spawned.tool=websearch status=completed part_id=prt_fake123 summary=Web search: Context7 MCP documentation"""
        from_base = super().as_summary_lines()
        tools = []
        if self.spawned_tools is None: self.spawned_tools = []
        for tool in self.spawned_tools:
            name, status, summary, part_id = tool.get("tool"), tool.get("state").get("status"), tool.get("state").get("title"), tool.get("id")
            tools.append([f"{self._name}: spawned.tool={name} status={status} part_id={part_id} summary={summary}"])
        lines =  self.extend_by_hooks([
            f"{self._name}: spawned_session_id={self.spawned_session_id} model={self.spawned_provider}/{self.spawned_model}",
            f"{self._name}: call_id={self.call_id} summary={self.summary}",
            f"{self._name}: spawned_tools:",
            *tools,
        ])
        return from_base + lines

@dataclasses.dataclass
class OpenCodeTurnStart(OpenCodePartMessageJson):
    """A turn-start part marking the beginning of an assistant turn in the conversation.
    
    Represents the start of an assistant's turn, and may include a git snapshot of the working
    directory at that moment. The snapshot, in principle, could be used to recreate the exact
    state of the files at the start of this turn, but to my knowledge the OpenCode system is
    not feature-enabled to create git snapshots at every assistant turn and should be presumed
    to not exist or not represent the instantaneous state of the worktree.
    
    Design choice: TurnStart always appears first in the sorted order of parts within an
    assistant message, serving as a delimiter for the conversation turn.
    
    Attributes:
        snapshot: str - Git commit hash of the snapshot at turn start (their path: snapshot, e.g., "8a5401bdde50e197eb3413d30e3aaaeaa6c83f8d")
    
    Inherited from OpenCodePartMessageJson:
        (see OpenCodePartMessageJson)
    """
    snapshot: str  # a git snapshot (of files and .git) stored in snapshot/
    _name = "turn_start"
    _oc_name = "step-start"
    _keys = OpenCodePartMessageJson._keys | dict( snapshot="snapshot")

    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this turn-start marker as a list of strings for conversation delimitation.
        Includes session_id, part_id, and snapshot hash for tracking the git state at the beginning of this assistant turn.
        This marker always appears first in sorted assistant message parts, serving as a delimiter between turns.
        The snapshot field may be empty as OpenCode does not appear to create git snapshots on every turn.
        The format uses "{self._name}: <field>=<value>" enabling grep-friendly log analysis.
        Example output:
            turn_start: session_id=ses_3f9a0c857ffeQV5bsrY34Qnq21 part_id=prt_c067c6b04002np2GC93CFiQCqf snapshot=8a5401bdde50e197eb3413d30e3aaaeaa6c83f8d"""
        return self.extend_by_hooks([f"{self._name}: session_id={self.session_id} part_id={self.part_id} snapshot={self.snapshot}"])

@dataclasses.dataclass
class OpenCodeTurnEnd(OpenCodePartMessageJson):
    """A turn-end part marking the end of an assistant turn in the conversation.
    
    Represents the completion of an assistant's turn, including token usage, cost information,
    and a git snapshot. This marks the end of one complete assistant response cycle.
    
    Design choice: TurnEnd always appears last in the sorted order of parts within an
    assistant message, serving as a delimiter for the conversation turn. The part contains
    a snapshot, although it is not clear whether this snapshot represents the snapshot at
    TurnStart, or a new snapshot at TurnEnd, and generally speaking OpenCode does not
    appear feature-enabled to be making a git snapshot on every turn.
    
    Notes:
        1. It is not clear by which logic reason initiates a turn end, i.e., tool-calls, but why?
        2. It is not clear what specifically token sizes and reasoning numbers represent, nor lines read.
    
    Attributes:
        snapshot: str - Git commit hash of the snapshot at turn end (their path: snapshot, e.g., "8a5401bdde50e197eb3413d30e3aaaeaa6c83f8d")
        reason: str - Reason the turn ended (their path: reason, e.g., "tool-calls")
        cost_usd: float - Cost incurred in USD (their path: cost, e.g., 0)
        tokens_input: int - Number of input tokens used (their path: tokens.input, e.g., 4621)
        tokens_output: int - Number of output tokens used (their path: tokens.output, e.g., 196)
        tokens_reasoning: int - Number of reasoning tokens used (their path: tokens.reasoning, e.g., 1)
        lines_read: int - Number of lines read from cache (their path: tokens.cache.read, e.g., 53512)
        lines_write: int - Number of lines written to cache (their path: tokens.cache.write, e.g., 0)
    
    Inherited from OpenCodePartMessageJson:
        (see OpenCodePartMessageJson)
    """
    snapshot: str
    reason: str
    cost_usd: float
    tokens_input: int
    tokens_output: int
    tokens_reasoning: int
    lines_read: int
    lines_write: int
    _name = "turn_end"
    _oc_name = "step-finish"
    _keys = OpenCodePartMessageJson._keys | dict(
        snapshot="snapshot",
        reason="reason",
        cost_usd="cost",
        tokens_input="tokens.input",
        tokens_output="tokens.output",
        tokens_reasoning="tokens.reasoning",
        lines_read="tokens.cache.read",
        lines_write="tokens.cache.write",
    )

    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this turn-end marker as a list of strings, capturing token usage and cost metrics.
        Includes the reason the turn ended (e.g., "tool-calls"), cost in USD, input/output token counts, and cache
        read/write line counts for understanding resource consumption. Also includes session_id, part_id, and snapshot
        hash for completeness. This marker always appears last in sorted assistant message parts, marking the end of
        a complete assistant response cycle. The format uses "{self._name}: <field>=<value>" for grep-friendly parsing.
        Example output:
            turn_end: reason=tool-calls cost=0.0025
            turn_end: tokens 4621 => 196
            turn_end: lines read 53512 and write 0
            turn_end: session_id=ses_3f9a0c857ffeQV5bsrY34Qnq21 part_id=prt_c067c6b04002np2GC93CFiQCqf, snapshot=8a5401bdde50e197eb3413d30e3aaaeaa6c83f8d"""
        return self.extend_by_hooks([
            f"{self._name}: reason={self.reason} cost={self.cost_usd}",
            f"{self._name}: tokens {self.tokens_input} => {self.tokens_output}",
            f"{self._name}: lines read {self.lines_read} and write {self.lines_write}",
            f"{self._name}: session_id={self.session_id} part_id={self.part_id}, snapshot={self.snapshot}",
        ])

@dataclasses.dataclass
class OpenCodeReasoning(OpenCodePartMessageJson):
    """A reasoning trace part containing the assistant's internal thought process.
    
    Represents the assistant's reasoning before generating a response. Includes timing
    information to determine execution order relative to other parts.
    
    Design choice: Reasoning parts are sorted by their timestamp when available, as they may appear
    before, after, or interleaved with tool calls. The text field may be empty in some cases.
    
    Attributes:
        time_start_ms: int - Unix timestamp when reasoning started (their path: time.start, e.g., 1769635343108)
        time_end_ms: int - Unix timestamp when reasoning ended (their path: time.end, e.g., 1769635346077)
        text: str - The reasoning content (their path: text, e.g., "")
    
    Inherited from OpenCodePartMessageJson:
        (see OpenCodePartMessageJson)
    """
    time_start_ms: int
    time_end_ms: int
    text: str
    _name = "reasoning"
    _oc_name = "reasoning"
    _keys = OpenCodePartMessageJson._keys | dict(time_start_ms="time.start", time_end_ms="time.end", text="text")

    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this reasoning trace as a list of strings for analyzing the assistant's thought process.
        Includes the part_id and timestamps (time_start_ms and time_end_ms) for ordering reasoning relative to other parts,
        and the full reasoning text demarcated with START/END markers to clearly delimit the content. Reasoning traces
        may be empty in some cases (e.g., when the model jumps directly to tool calls). The format uses
        "{self._name}: <field>=<value>" for grep-friendly log parsing, with demarcation enabling content extraction.
        Example output:
            reasoning: part_id=prt_c067c6b04002np2GC93CFiQCqf time_start_ms=1769635343108 time_end_ms=1769635346077
            reasoning: ----- DEMARCATION OF TEXT_START -----
            reasoning: text=I need to explore the codebase to understand the structure before making changes...
            reasoning: ----- DEMARCATION OF TEXT_END -----"""
        return self.extend_by_hooks([
            f"{self._name}: part_id={self.part_id} time_start_ms={self.time_start_ms} time_end_ms={self.time_end_ms}",
            f"{self._name}: ----- DEMARCATION OF TEXT_START -----",
            f"{self._name}: text={self.text}",
            f"{self._name}: ----- DEMARCATION OF TEXT_END -----",
        ])


@dataclasses.dataclass
class OpenCodePatch(OpenCodePartMessageJson):
    """A patch part listing files modified during the assistant's turn.
    
    Represents a summary of files that were modified during an assistant turn. Contains
    the git hash of the patch and a list of affected file paths. Note: This does NOT
    contain the actual diff content - that information is stored separately in session_diff.
    
    Design choice: Patch parts do not have timestamps, so they are placed after timestamped
    parts (reasoning, tools) but before text in the sorted order.
    
    Attributes:
        hash: str - Git hash of the patch (their path: hash, e.g., "18411f23e94607fdb7adf8c9e15e9b4adecbbe9d")
        filenames: list[str] - List of file paths modified (their path: files, e.g., ["/path/to/file.py"])
    
    Inherited from OpenCodePartMessageJson:
        (see OpenCodePartMessageJson)
    """
    # NOTE: Patch contains only file list and git hash—not actual diff content. Actual file diffs
    # (before/after content) are available via session_diff/ at the session level (see FileModification class).
    hash: str
    filenames: list[str]
    _name = "patch"
    _oc_name = "patch"
    _keys = OpenCodePartMessageJson._keys | dict(hash="hash", filenames="files")

    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this patch as a list of strings, listing files modified during this assistant turn.
        Includes the part_id, git hash of the patch, and count of files modified, followed by each filename on its own line.
        Note: This does NOT contain the actual diff content—file diffs are stored separately in session_diff (see FileModification class).
        Patch parts lack timestamps, so they are placed after timestamped parts (reasoning, tools) in sorted order.
        The format uses "{self._name}: <field>=<value>" for grep-friendly log analysis.
        Example output:
            patch: part_id=prt_c067c6b04002np2GC93CFiQCqf hash=18411f23e94607fdb7adf8c9e15e9b4adecbbe9d n_files=3
            patch: filename=/src/utils/helper.py
            patch: filename=/src/main.py
            patch: filename=/tests/test_helper.py"""
        lines =[f"{self._name}: part_id={self.part_id} hash={self.hash} n_files={len(self.filenames)}"]
        lines.extend([f"{self._name}: filename={f}" for f in self.filenames])
        return self.extend_by_hooks(lines)

@dataclasses.dataclass
class OpenCodeFile(OpenCodePartMessageJson):
    """A file reference part representing a file attachment in a user prompt.
    
    Represents a file that was attached to or referenced in a user message, including
    its MIME type and location within the original message text.
    
    Design choice: File parts are sorted by their location.start within the message text,
    allowing reconstruction of the original prompt order.
    
    Attributes:
        mime: str - MIME type of the file (their path: mime, e.g., "text/plain")
        stub: str - File reference as file:// URL (their path: url, e.g., "file:///path/to/file.py")
        location: dict - Position in parent message (their path: source.text, e.g., {"start": 0, "end": 50, "value": "@file.py"})
    
    Inherited from OpenCodePartMessageJson:
        (see OpenCodePartMessageJson)
    """
    mime: str
    stub: str  # the filename as a file:// to account for future expansions to URLs etc.
    location: dict  # the dict(start, end, value) inside its parent message (which we do not have the msg_id for)
    _name = "file"
    _oc_name = "file"
    _keys = OpenCodePartMessageJson._keys | dict(mime="mime", stub="url", location="source.text")

    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this file attachment reference as a list of strings for tracking user-provided files.
        Includes the part_id and location dict (with start/end positions in the parent message) for reconstructing
        the original prompt order, along with MIME type and the file stub (as a file:// URL). File parts are
        sorted by location.start in user messages, allowing accurate prompt reconstruction. The stub field contains
        a file:// URL for local files. The format uses "{self._name}: <field>=<value>" for grep-friendly parsing.
        Example output:
            file: part_id=prt_c067c6b04002np2GC93CFiQCqf location={'start': 0, 'end': 50, 'value': '@file.py'}
            file: mime=text/plain stub=file:///path/to/project/file.py"""
        return self.extend_by_hooks([
            f"{self._name}: part_id={self.part_id} location={self.location}",
            f"{self._name}: mime={self.mime} stub={self.stub}",
        ])

@dataclasses.dataclass
class OpenCodeSubAgent(OpenCodePartMessageJson):
    """A subagent reference part representing an @agent mention in a user prompt.
    
    Represents a subagent that was referenced using @ notation in a user message,
    including its name and location within the original message text.
    
    Design choice: SubAgent parts are sorted by their location.start within the message text,
    allowing reconstruction of the original prompt order. Note: the name field contains the
    subagent path-as-name (e.g., "path/to/subagent/researcher") whereas an agent by that same 
    location would be referred to simply as "researcher" in various documentation and fields 
    throughout OpenCode. At this point we have no idea for the discrepancy. Also note: the 
    _oc_name is "agent", but its function is always subagent, whereas the OpenCode agents are
    components at the message-level anyway.
    
    Attributes:
        name: str - Name/path of the subagent (their path: name, e.g., "subagent/researcher")
        location: dict - Position in parent message (their path: source, e.g., {"start": 31, "end": 51, "value": "@subagent/researcher"})
    
    Inherited from OpenCodePartMessageJson:
        (see OpenCodePartMessageJson)
    """
    name: str
    location: dict  # the dict(start, end, value) inside its parent message (which we do not have the msg_id for)
    _name = "subagent"
    _oc_name = "agent"
    _keys = OpenCodePartMessageJson._keys | dict(name="name", location="source")

    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this subagent reference as a list of strings for tracking agent invocations in user prompts.
        Includes the part_id, subagent name (as a path like "subagent/researcher"), and location dict (with start/end
        positions and the original @mention value) for reconstructing the original prompt order. Note: the _oc_name
        is "agent" but this class handles subagent references, not message-level agents. SubAgent parts are sorted by
        location.start in user messages. The format uses "{self._name}: <field>=<value>" for grep-friendly parsing.
        Example output:
            subagent: part_id=prt_c067c6b04002np2GC93CFiQCqf name=subagent/researcher location={'start': 31, 'end': 51, 'value': '@subagent/researcher'}"""
        return self.extend_by_hooks([f"{self._name}: part_id={self.part_id} name={self.name} location={self.location}"])

def sort_user_message_parts_approximately(parts: list[OpenCodePartMessageJson]) -> list[OpenCodePartMessageJson]:
    """Approximates the original ordering of parts within a user message.
    
    UserMessage parts consist of OpenCodeText (the prompt), OpenCodeFile (attached files),
    and OpenCodeSubAgent (referenced agents). Text has no location; files and subagents
    are sorted by their position in the original prompt using location.start.
    
    Design choice: Timestamps are not included in all user message parts, so we must rely 
    on knowledge of which parts may or may not be in UserMessage and reasonable assumptions 
    about how those are intended to be oriented in their display. Not all Part types are 
    presumed to be in UserMessage.
    """
    # UserMessage may have parts of Text (the text), Files (added Files), and Agents (referenced agents)
    # both files and subagents can be sorted according to their location in the text using p.location.get("start")
    as_sorted = []
    [as_sorted.append(p) for p in parts if p.__class__ in [OpenCodeText]]
    files = [p for p in parts if p.__class__ in [OpenCodeFile]]
    files = sorted(files, key=lambda p: p.location.get("start") if p.location else -1)
    as_sorted.extend(files)
    subagents = [p for p in parts if p.__class__ in [OpenCodeSubAgent]]
    subagents = sorted(subagents, key=lambda p: p.location.get("start") if p.location else -1)
    as_sorted.extend(subagents)
    return as_sorted

def sort_assistant_message_parts_approximately(parts: list[OpenCodePartMessageJson]) -> list[OpenCodePartMessageJson]:
    """Approximates the original ordering of parts within an assistant message.
    
    AssistantMessage parts contain OpenCodeTurnStart, OpenCodeReasoning, OpenCodeTool,
    OpenCodePatch, OpenCodeText, and OpenCodeTurnEnd. Only Reasoning and Tool have timestamps;
    Patch and Text do not. TurnStart and TurnEnd delimit the conversation turn.
    
    Design choice: Timestamps are not included in all assistant message parts, so we must rely 
    on knowledge of which parts may or may not be in AssistantMessage and reasonable assumptions 
    about how those are intended to be oriented in their display. Not all Part types are 
    presumed to be in AssistantMessage.
    """
    as_sorted = []
    [as_sorted.append(p) for p in parts if p.__class__ in [OpenCodeTurnStart]]
    with_timestamps = [p for p in parts if p.__class__ in [OpenCodeReasoning, OpenCodeTool]]
    with_timestamps = sorted(with_timestamps, key=lambda p: p.time_start_ms if p.time_start_ms else -1)
    as_sorted.extend(with_timestamps)
    [as_sorted.append(p) for p in parts if p.__class__ in [OpenCodePatch]]
    [as_sorted.append(p) for p in parts if p.__class__ in [OpenCodeText]]
    [as_sorted.append(p) for p in parts if p.__class__ in [OpenCodeTurnEnd]]
    [as_sorted.append(p) for p in parts if p not in as_sorted]
    return as_sorted




##########################################################################################
# The following are dataclasses for METADATA data, which encapselate the majority of the
# interactions between the user and assistant, under one session_id, including the creation
# of subagents and workflow _todo sequences, and its summaries, excluding message content.
# The summaries are quite extensive, including, but not limited to, one-line summary for
# each user prompt (message), file diffs (including content changes, but not tool calls),
# cost and content metrics (i.e., size, tokens used). We provide as_summary_lines to aid
# an external summary of a session, i.e., useful for document handling otherwise. For our
# own purposes we simply provide our unknowns and a point to a prototypical file on disk.
##########################################################################################

@dataclasses.dataclass
class _OpenCodeSessionConstruct:
    """Session-level metadata construct serving as the base class for Project, Session, SessionToDo, and FileModification.

    This base class provides common infrastructure for dataclasses that represent session-level data rather
    than message-part data, including the _name attribute for display purposes and the _keys attribute for
    JSON path mapping. See description of simple_wrangler in OpenCodePartMessageJson.

    Design choice: This base class provides the _keys pattern at the session/metadata level, similar to how
    OpenCodePartMessageJson provides it at the message-part level.

    Attributes:
        _name: str - Our display name used in printed summaries
        _keys: dict - Maps our attribute names to their JSON paths
    """
    _name = "generic"
    _keys = {}  # overwrite these both inherited class

    def as_summary_lines(self) -> list[str]:
        """Generates a human-readable summary of this session-level construct as a list of strings.
        This is the base implementation that returns a placeholder message indicating no additional content.
        Subclasses override this method to provide specific summaries including identifiers, timestamps,
        and content-specific information. The format is "{self._name}: <field>=<value>" enabling
        grep-friendly log parsing across all session-level types.
        Example output:
            generic: no additional content provided"""
        return [f"{self._name}: no additional content provided"]  # overwrite in inherited class


@dataclasses.dataclass
class Project(_OpenCodeSessionConstruct):
    """A project represents a root directory from which OpenCode is launched, tracking its metadata and lifetime.

    A project exists for each unique root directory where OpenCode runs, storing its version control system,
    sandboxed environments, and creation/update timestamps. Projects are the top-level organizational unit
    in OpenCode's data model.

    Design choice: Projects use SHA-based identifiers for uniqueness. The vcs field captures which version
    control system is in use (typically "git"). Sandboxes are preserved as-is even when empty lists.

    Attributes:
        project_id: str - Unique SHA-based identifier (their path: id, e.g., "60d8e4929ee0fe0a826b6df31ea8c721dfee3b80")
        worktree: str - Absolute filesystem path where OpenCode runs (their path: worktree, e.g., "/path/to/project")
        vcs: str - Version control system in use (their path: vcs, e.g., "git")
        sandboxes: list[str] - List of sandboxed environments, presumably Daytona droplets/containers (their path: , e.g., [])
        time_start_ms: int - Unix timestamp when project was created (their path: time.created, e.g., 1769385986799)
        time_end_ms: int - Unix timestamp of last update to project (their path: time.updated, e.g., 1770949317336)

    Inherited from _OpenCodeSessionConstruct:
        (see _OpenCodeSessionConstruct)
    """
    project_id: str
    worktree: str
    vcs: str
    sandboxes: list[str]
    time_start_ms: int
    time_end_ms: int

    _name = "project"
    _keys = dict(project_id="id",
                 worktree="worktree",
                 vcs="vcs",
                 sandboxes="",  # intentionally empty - no sandbox environments extracted at this time
                 time_start_ms="time.created",
                 time_end_ms="time.updated")

    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this project as a list of strings for project-level analysis.
        Includes the version control system (vcs), worktree path, sandbox list (which may be empty), timestamps
        for creation and last update, and computed duration in seconds. This is the top-level organizational
        unit in OpenCode's data model, used to group sessions. The format uses "{self._name}: <field>=<value>"
        for grep-friendly log parsing.
        Example output:
            project: vcs=git worktree=/Users/davidbernat/gitlab/OpenCode
            project: sandboxes=[]
            project: time_start_ms=1769385986799 time_end_ms=1770949317336 duration_s=155.56
            project: project id=60d8e4929ee0fe0a826b6df31ea8c721dfee3b80"""
        duration_s = (self.time_end_ms - self.time_start_ms)/1000
        return [
            f"{self._name}: vcs={self.vcs} worktree={self.worktree}",
            f"{self._name}: sandboxes={self.sandboxes}",
            f"{self._name}: time_start_ms={self.time_start_ms} time_end_ms={self.time_end_ms} duration_s={duration_s:.2f}",
            f"{self._name}: project id={self.project_id}",
        ]

@dataclasses.dataclass
class Permission:
    """Permission rule for session actions, controlling access based on action type and glob patterns.

    Permissions define what actions OpenCode allows, denies, or prompts for user confirmation,
    using glob patterns to match command lines or tool invocations. Each permission specifies
    an action type (e.g., "todowrite", "task"), an access level, and a matching pattern.

    Design choice: Permissions are stored as a flat list in sessions, allowing straightforward
    checking against user-defined constraints. The pattern uses glob syntax for flexible matching.

    Attributes:
        action: str - Whether action is allowed, denied, or requires confirmation (their path: action, e.g., "allow")
        pattern: str - Glob patterns to match against underlying command executables of the overall permission (their path: pattern, e.g., "git *")
        permission: str - Type of permission being controlled (their path: permission, e.g., "bash")
    """
    action: str
    pattern: str
    permission: str
    _name = "permission"
    _keys = dict(action="action",
                 pattern="pattern",
                 permission="permission")

    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this permission rule as a list of strings for understanding session access control.
        Includes the action (allow/deny/prompt), glob pattern for matching command lines or tool invocations,
        and the permission type (e.g., bash, todowrite, task). Permissions are stored as a flat list in sessions
        and control what actions OpenCode allows, denies, or prompts for confirmation. The format uses
        "{self._name}: <field>=<value>" for grep-friendly log analysis.
        Example output:
            permission: action=allow pattern="git" * permission=bash"""
        return [f"{self._name}: action={self.action} pattern=\"{self.pattern}\" permission={self.permission}"]


@dataclasses.dataclass
class SessionToDo:
    """A workflow task managed by OpenCode's built-in todo agent for session-level task sequencing.

    SessionToDo objects represent individual Tasks created by the OpenCode workflow engine, which may or may not
    spawn SubAgents, which may or may not invoke new session creation to minimize context impact in the primary session,
    using the OpenCode todowrite tool. Each task has a todo_id, content description, priority level, and completion status.
    These are persisted in storage/todo/{session_id}.json when present.

    Design choice: Tasks can be identified by numeric strings (e.g., "1", "2") or named identifiers.
    The status field indicates whether the task is pending, in_progress, or completed.

    Attributes:
        todo_id: str - Unique identifier for the task (their path: id, e.g., "1")
        purpose: str - Human-readable task description (their path: content, e.g., "Get repositories via GitHub MCP")
        priority: str - Priority level as string (their path: priority, e.g., "high")
        status: str - Current task state (their path: status, e.g., "completed")

    Inherited from _OpenCodeSessionConstruct:
        (see _OpenCodeSessionConstruct)
    """
    todo_id: str
    purpose: str
    priority: str
    status: str
    _name = "workflow_todo"
    _keys = dict(todo_id="id",
                 purpose="content",
                 priority="priority",
                 status="status")

    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this workflow task as a list of strings for tracking task progression.
        Includes the todo_id (numeric string like "1" or named identifier), purpose (human-readable task description),
        priority level (e.g., "high", "medium", "low"), and current status (pending, in_progress, completed).
        Tasks are created by the OpenCode workflow engine using the todowrite tool and persisted in storage/todo/{session_id}.json.
        The format uses "{self._name}: <field>=<value>" for grep-friendly log analysis.
        Example output:
            workflow_todo: todo_id=1 purpose=Get repositories via GitHub MCP
            workflow_todo: priority=high status=completed"""
        return [
            f"{self._name}: todo_id={self.todo_id} purpose={self.purpose}",
            f"{self._name}: priority={self.priority} status={self.status}",
        ]


@dataclasses.dataclass
class FileModification:
    """A single file modification within a session, capturing before/after file contents and line counts.

    FileModification objects represent changes to a file during an OpenCode session, storing the filename,
    the full file content before and after this specific edit, and line-level statistics. These are aggregated
    from the tool.edit and tool.write operations throughout the session and stored in storage/session_diff/{session_id}.json.
    Each FileModification represents one edit operation within the session, not cumulative session start/end state.

    Design choice: The content_before and content_after fields contain the actual file contents at the time of this
    specific edit, not unified diff format. This allows viewing exactly what the file looked like before and after
    each edit operation. Line counts provide quick statistical overview without parsing file contents.

    Attributes:
        filename: str - Relative or absolute path to the file (their path: file, e.g., "src/utils/helper.py")
        content_before: str - Full file content before this edit (their path: before, e.g., "#!/usr/bin/env python3\n\"\"\"\nOld content here\n\"\"\"\n\ndef hello():\n    pass")
        content_after: str - Full file content after this edit (their path: after, e.g., "#!/usr/bin/env python3\n\"\"\"\nNew content here\n\"\"\"\n\ndef hello():\n    return True")
        n_lines_added: int - Count of lines added in this edit (their path: additions, e.g., 3)
        n_lines_deleted: int - Count of lines deleted in this edit (their path: deletions, e.g., 1)

    Inherited from _OpenCodeSessionConstruct:
        (see _OpenCodeSessionConstruct)
    """
    filename: str
    content_before: str  # contents of file_diff at start of operation
    content_after: str    # contents of file_diff at end of operation
    n_lines_added: int   # presumable number of LINES added
    n_lines_deleted: int # presumable number of LINES removed
    _name = "file_diff"
    _keys = dict(filename="file",
                 content_before="before",
                 content_after="after",
                 n_lines_added="additions",
                 n_lines_deleted="deletions")

    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this file modification as a list of strings for tracking code changes.
        Includes the filename and line count statistics (lines added and deleted) for quick statistical overview.
        Note: The full content_before and content_after fields are NOT included in the summary (they can be
        accessed directly on the object for detailed diff viewing). FileModifications are aggregated from
        tool.edit and tool.write operations and stored in storage/session_diff/{session_id}.json.
        The format uses "{self._name}: <field>=<value>" for grep-friendly log analysis.
        Example output:
            file_diff: filename=src/utils/helper.py
            file_diff: n_lines_added=3 n_lines_deleted=1"""
        return [
            f"{self._name}: filename={self.filename}",
            f"{self._name}: n_lines_added={self.n_lines_added} n_lines_deleted={self.n_lines_deleted}",
        ]



@dataclasses.dataclass
class _MessageBase:
    """Base class for all message types, providing common fields inherited by UserMessage and AssistantMessage.

    _MessageBase provides the shared infrastructure for messages, including message_id, role, timestamp,
    agent, provider, model, and the parts list. It serves as the foundation for both user-originated
    and assistant-generated messages in OpenCode's conversation model.

    Design choice: The parts field is initially None for performance reasons and populated separately
    via hydrate_message_part_from_str. Model and provider use different accessors for assistant messages.

    Attributes:
        message_id: str - Unique message identifier prefixed with msg_ (their path: id, e.g., "msg_be1259b8a001ZcOuUgrPxnYgk7")
        role: str - Operator role driving message creation (their path: role, e.g., "user")
        timestamp_ms: int - Unix timestamp when message was created (their path: time.created, e.g., 1769008896916)
        agent: str - Which agent processes/created the message (their path: agent, e.g., "build")
        provider: str - AI provider used (their path: model.providerID, e.g., "opencode")
        model: str - Specific model identifier (their path: model.modelID, e.g., "big-pickle")
        parts: list[OpenCodePartMessageJson] - List of message parts (their path: , not directly deserialized)
    """
    message_id: str
    role: str
    timestamp_ms: int
    agent: str
    provider: str
    model: str
    parts: list[OpenCodePartMessageJson]  # added manually; not deserialized for speed reasons
    _name = None
    _keys = dict(message_id="id",
                 role="role",
                 timestamp_ms="time.created",
                 agent="agent",
                 model="model.modelID",        # assistant messages use different accessor for model
                 provider="model.providerID",  # assistant messages use different accessor for provider
                 parts="")  # ignore, gets set to None until later

    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this message as a list of strings for conversation analysis.
        This is the base implementation including timestamp, agent, and model (in provider/model format).
        Subclasses (UserMessage, AssistantMessage) override to add role-specific fields like parent_id,
        file_diffs, or parts. The parts list is populated separately via hydrate_message_part_from_str
        for performance. The format uses "{self._name}: <field>=<value>" for grep-friendly parsing.
        Example output:
            (base): timestamp_ms=1769008896916 agent=build model=opencode/big-pickle"""
        return [
            f"{self._name}: timestamp_ms={self.timestamp_ms} agent={self.agent} model={self.provider}/{self.model}",

        ]

@dataclasses.dataclass
class UserMessage(_MessageBase):
    """User-originated message containing the user's prompt, summary title, and any file modifications.

    UserMessage represents a user-initiated message in the conversation, including the raw prompt text,
    a summary title generated after message creation, and any file modifications resulting from the
    user's request. UserMessages are stored in storage/message/{session_id}/ as msg_*.json files.

    Design choice: UserMessages include a summary title that provides a quick overview.
    File modifications are calculated, presumably, from the aggregated
    tool.edit, tool.write, etc., operations that occur in response to this message.

    Attributes:
        title: str - Unique summary for the message generated after message creation (their path: summary.title, e.g., "Exporting session logs via /export command")
        file_diffs: list[FileModification] - List of FileModifications created by actions in this message (their path: summary.diffs, e.g., [FileModification(...), ...])

    Inherited from _MessageBase:
        message_id: str - Unique message identifier prefixed with msg_ (their path: id, e.g., "msg_be1259b8a001ZcOuUgrPxnYgk7")
        role: str - Always "user" for UserMessage (their path: role, e.g., "user")
        timestamp_ms: int - Unix timestamp when message was created (their path: time.created, e.g., 1769008896916)
        agent: str - Which agent processes/created the message (their path: agent, e.g., "build")
        provider: str - AI provider used (their path: model.providerID, e.g., "opencode")
        model: str - Specific model identifier (their path: model.modelID, e.g., "big-pickle")
        parts: list[OpenCodePartMessageJson] - List of message parts (their path: , not directly deserialized)
    """
    title: str
    file_diffs: list[FileModification]
    _name = "user_message"
    _keys = _MessageBase._keys | dict(title="summary.title", file_diffs="summary.diffs")

    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this user message as a list of strings for conversation analysis.
        Includes timestamp_ms, message_id, agent, model (provider/model format), summary title, and count of
        file modifications. Then iterates through file_diffs (each FileModification's summary) and all parts
        (indented with two spaces) to provide complete context of what the user requested and what changed.
        This captures the user's prompt, the summary generated after creation, and any files modified in
        response to this message. The format uses "{self._name}: <field>=<value>" for grep-friendly parsing.
        Example output:
            user_message: timestamp_ms=1769008896916 message_id=msg_be1259b8a001ZcOuUgrPxnYgk7 agent=build model=opencode/big-pickle
            user_message: summary=Exporting session logs via /export command
            user_message: n_files_modified=2
              file_diff: filename=src/utils/helper.py
              file_diff: n_lines_added=3 n_lines_deleted=1
              file_diff: filename=src/main.py
              file_diff: n_lines_added=10 n_lines_deleted=5
              text: part_id=prt_abc123 time_start_ms=1769008896916 time_end_ms=1769008896916
              text: ----- DEMARCATION OF TEXT_START -----
              text: Please export the session logs...
              text: ----- DEMARCATION OF TEXT_END -----"""
        summary = [
            f"{self._name}: timestamp_ms={self.timestamp_ms} message_id={self.message_id} agent={self.agent} model={self.provider}/{self.model}",
            f"{self._name}: summary={self.title}",
            f"{self._name}: n_files_modified={len(self.file_diffs)}",
        ]
        for fd in self.file_diffs: summary.extend(fd.as_summary_lines())
        for p in self.parts: [summary.append(f"  {line}") for line in p.as_summary_lines()]  # indent slightly
        return summary

@dataclasses.dataclass
class AssistantMessage(_MessageBase):
    """Agent message with operational metadata including token usage, cost, and message parts.

    AssistantMessage represents an assistant-generated response in the conversation, including token usage
    statistics, cost information, the parent user message that triggered this response, and the parts
    that make up the response (reasoning, tool calls, text, etc.). AssistantMessages are stored in
    storage/message/{session_id}/ as msg_*.json files.

    Design choice: AssistantMessages include detailed token breakdowns and cost tracking for monitoring
    resource usage. The parts list contains the complete response structure including turn markers,
    reasoning traces, tool invocations, and output text.

    Attributes:
        parent_id: str - The message_id of the user message that triggered this agent response (their path: parentID, e.g., "msg_be1259b8a001ZcOuUgrPxnYgk7")
        cwd: str - Current working directory at time of message execution (their path: path.cwd, e.g., "/path/to/project")
        cost: float - USD (or equivalent) cost incurred for this message operation (their path: cost, e.g., 0.0025)
        tokens: dict - Token usage breakdown (their path: tokens, e.g., {"input": 4621, "output": 196, "reasoning": 1, "cache": {"read": 53512, "write": 0}})

    Inherited from _MessageBase:
        message_id: str - Unique message identifier prefixed with msg_ (their path: id, e.g., "msg_c067c5e73001E2HgIdArAgeWfm")
        role: str - Always "assistant" for AssistantMessage (their path: role, e.g., "assistant")
        timestamp_ms: int - Unix timestamp when message was created (their path: time.created, e.g., 1769008896916)
        agent: str - Which agent processes/created the message (their path: agent, e.g., "build")
        provider: str - AI provider used (their path: model.providerID, e.g., "opencode")
        model: str - Specific model identifier (their path: model.modelID, e.g., "big-pickle")
        parts: list[OpenCodePartMessageJson] - List of message parts (their path: , not directly deserialized)
    """
    parent_id: str  # seems to be the user message which spawned the actions
    cwd: str
    cost: float
    tokens: dict
    _name = "assistant_message"
    _keys = _MessageBase._keys | dict(parent_id="parentID",
                                      cwd="path.cwd",
                                      cost="cost",
                                      tokens="tokens",
                                      model="modelID",        # these are overwritten from _MessageBase
                                      provider="providerID")  # these are overwritten from _MessageBase

    def as_summary_lines(self) -> list[str]:
        """Generates a summary of this assistant message as a list of strings for conversation analysis.
        Includes timestamp_ms, message_id, agent, model (provider/model format), the parent_id (user message
        that triggered this response), current working directory, cost in USD, and full token breakdown
        (input, output, reasoning, cache read/write). Then iterates through all parts (indented with two spaces)
        to provide complete context of the assistant's reasoning, tool calls, and output. This captures the
        complete assistant response cycle. The format uses "{self._name}: <field>=<value>" for grep-friendly parsing.
        Example output:
            assistant_message: timestamp_ms=1769008897000 message_id=msg_c067c5e73001E2HgIdArAgeWfm agent=build model=opencode/big-pickle
            assistant_message: parent_id=msg_be1259b8a001ZcOuUgrPxnYgk7 cwd=/Users/davidbernat/gitlab/OpenCode
            assistant_message: cost=0.0025 tokens={'input': 4621, 'output': 196, 'reasoning': 1, 'cache': {'read': 53512, 'write': 0}}
              turn_start: session_id=ses_abc123 part_id=prt_start123 snapshot=8a5401bd
              reasoning: part_id=prt_reason123 time_start_ms=1769008897001 time_end_ms=1769008897010
              reasoning: ----- DEMARCATION OF TEXT_START -----
              reasoning: text=I need to explore the codebase first...
              reasoning: ----- DEMARCATION OF TEXT_END -----
              tool_use: name=tool.bash status=completed duration_s=0.45
              tool_use: call_id=call_123 summary=List files"""
        summary = [
            f"{self._name}: timestamp_ms={self.timestamp_ms} message_id={self.message_id} agent={self.agent} model={self.provider}/{self.model}",
            f"{self._name}: parent_id={self.parent_id} cwd={self.cwd}",
            f"{self._name}: cost={self.cost} tokens={self.tokens}",
        ]
        for p in self.parts: [summary.append(f"  {line}") for line in p.as_summary_lines()]  # indent slightly
        return summary

@dataclasses.dataclass
class Session:
    """A complete OpenCode session container, linking project, todos, file changes, and messages and their parts.

    Session represents an entire OpenCode interaction, containing the project reference, workflow tasks,
    file modifications, and all messages exchanged during the session. Sessions are stored in
    storage/session/{project_id}/{session_id}.json files.

    Design choice: Sessions serve as the top-level organizational unit for analysis, containing references
    to the Project, list of SessionToDo items, all file modifications, and the complete message history.
    Session data objects can exist with two levels of hydration. Unhydrated contains only the Session and
    Project metadata which exist in their ses_*.json and project_*.json log files, respectively. No
    information about messages, todos, etc., nor their over-arching metadata is present. Hydrated contains
    the thorough top to tip completion of all relevant data structures within the Session object.

    Attributes:
        session_id: str - Unique session identifier prefixed with ses_ (their path: id, e.g., "ses_3ac09ec82ffeEgy0wXJS0EcEEx")
        parent_id: str - session_id of parent session if this was spawned as subagent (their path: parentID, e.g., "ses_404bba5b0ffeKNLSHk5AZw1eh3")
        slug: str - Auto-generated memorable name (their path: slug, e.g., "wesley-knight")
        title: str - User-provided or auto-generated session title (their path: title, e.g., "TD Bank: Week 4 Lawyer Contacts")
        time_start_ms: int - Unix timestamp when session opened (their path: time.created, e.g., 1770935161728)
        time_end_ms: int - Unix timestamp when session closed/updated (their path: time.updated, e.g., 1770939447842)
        project: Project - Linked Project object (their path: , derived from projectID in file path)
        todos: list[SessionToDo] - List of SessionToDo items for this session (their path: , loaded from storage/todo/{session_id}.json)
        file_diffs: list[FileModification] - List of FileModification objects (their path: , loaded from storage/session_diff/{session_id}.json)
        messages: list[_MessageBase] - List of all UserMessage and AssistantMessage objects (their path: , loaded from storage/message/{session_id}/)
        version: str - OpenCode version used for session (their path: version, e.g., "1.1.63")
        permissions: list[Permission] - Lists of actions which are permitted or denied (their path: permission)
    """
    session_id: str
    parent_id: str
    version: str
    slug: str
    title: str
    time_start_ms: int
    time_end_ms: int
    permissions: list[Permission]
    project: Project = None
    todos: list[SessionToDo] = None
    file_diffs: list[FileModification] = None
    messages: list[_MessageBase] = None
    _name = "session"
    _keys = dict(session_id="id",
                 parent_id="parentID",
                 slug="slug",
                 title="title",
                 time_start_ms="time.created",
                 time_end_ms="time.updated",
                 version="version",
                 permissions="permission",)

    def as_summary_lines(self) -> list[str]:
        """Generates a comprehensive summary of this session as a list of strings for complete session analysis.
        Includes session title, parent_id (if spawned as subagent), session_id, slug, OpenCode version, timestamps
        for start and end, and computed duration in seconds. Then iterates through permissions, project info,
        todos (workflow tasks), file_diffs (file modifications), and all messages (user and assistant) in sequence.
        This produces a complete narrative of the entire session from open to close, suitable for logging, debugging,
        or feeding to downstream systems like memory layers. The format uses "{self._name}: <field>=<value>" for
        grep-friendly parsing, with nested summaries indented appropriately.
        Example output:
            session: title=My OpenCode Session
            session: parent_id=ses_parent123 session_id=ses_3ac09ec82ffeEgy0wXJS0EcEEx slug=my-session version=1.1.63
            session: time_start_ms=1770935161728 time_end_ms=1770939447842 duration_s=4285.14
            permission: action=allow pattern="git *" permission=bash
            project: vcs=git worktree=/Users/davidbernat/project
            project: sandboxes=[]
            project: time_start_ms=1769385986799 time_end_ms=1770949317336 duration_s=1553337.04
            project: project id=60d8e4929ee0fe0a826b6df31ea8c721dfee3b80
            workflow_todo: todo_id=1 purpose=Implement feature X
            workflow_todo: priority=high status=completed
            user_message: timestamp_ms=1770935161800 message_id=msg_user123 agent=build model=opencode/big-pickle
            user_message: summary=Implement the new feature
            assistant_message: timestamp_ms=1770935162000 message_id=msg_assist123 agent=build model=opencode/big-pickle
            assistant_message: cost=0.005 tokens=..."""
        duration_s = (self.time_end_ms - self.time_start_ms)/1000
        summary = [
            f"{self._name}: title={self.title}",
            f"{self._name}: parent_id={self.parent_id} session_id={self.session_id} slug={self.slug} version={self.version}",
            f"{self._name}: time_start_ms={self.time_start_ms} time_end_ms={self.time_end_ms} duration_s={duration_s:.2f}"
        ]
        [summary.extend(p.as_summary_lines()) for p in self.permissions]
        summary.extend(self.project.as_summary_lines())
        [summary.extend(td.as_summary_lines()) for td in self.todos]
        [summary.extend(fd.as_summary_lines()) for fd in self.file_diffs]  # probably this first, not last, as summary
        [summary.extend(m.as_summary_lines()) for m in self.messages]
        return summary



# List of supported OpenCodePartMessageJson subclasses for parsing message parts.
# Used by hydrate_message_part_from_str to map JSON "type" field to Python classes.
_supported: list[OpenCodePartMessageJson] = [OpenCodeTurnStart, OpenCodeText, OpenCodeTool, OpenCodeReasoning, OpenCodeTurnEnd,
                                      OpenCodePatch, OpenCodeFile, OpenCodeSubAgent]
# List of specialized tool subclasses that require custom handling beyond generic OpenCodeTool.
# Currently, contains only OpenCodeToolTask for subagent task spawning.
_specialized_tools: list[OpenCodePartMessageJson] = [OpenCodeToolTask]
def hydrate_message_part_from_str(line: str, supported: list[OpenCodePartMessageJson] | None = None) -> OpenCodePartMessageJson:
    """Parses a prt_*.json string into its corresponding OpenCodePartMessageJson subclass instance.
    
    Strips ANSI color codes from the input line, parses JSON, then matches the "type" field
    to either a supported Part class or a specialized tool subclass. For tool types, checks
    for specialized handlers (e.g., tool.task) before falling back to the generic OpenCodeTool.
    
    Design choice: The function prioritizes specialized tool types (i.e., tool.task) to provide 
    guaranteed access to tool-specific attribute fields, and specifically for tool.task to provide 
    a mechanism for identifying and managing when subagents are activated (i.e., new sessions 
    with new session_ids are spawned within an overarching session with its own session_id). 
    The supported part type is keyed by _oc_name for matching against the raw JSON "type" field 
    in OpenCode's JSON output.
    """
    if supported is None: supported = _supported
    cleaned = re.sub(r"\x1B\[[0-9;]*m", "", line.strip())  # this byte changes text color; so is removed
    unpacked = json.loads(cleaned)
    supported = {s._oc_name: s for s in supported}
    if "tool_use" in supported: supported["tool"] = supported["tool_use"]  # legacy declaration Apr 2026
    specialized = {s._oc_name: s for s in _specialized_tools}
    _type_str = unpacked.get("type")
    # if tools, check if we have specialized tools
    cls_str = None
    if _type_str == "tool":  # todowrite
        _specialized_str = _type_str + "." + unpacked.get("tool")
        cls_str = specialized.get(_specialized_str)  # returns None when tool.{name} is not a specialized tool
    if cls_str is None:
        cls_str = supported.get(_type_str)  # for when type != tool or tool.{name} is not a specialized tool
    if cls_str is not None:
        return recursive_constructor(unpacked, cls_str)
    raise ValueError(f"unrecognized message part type={_type_str}")

