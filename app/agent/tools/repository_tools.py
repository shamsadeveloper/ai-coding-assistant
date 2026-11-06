"""Read-only tools that let the agent explore the selected repository."""

from collections import Counter

from pydantic import BaseModel, Field

from app.agent.tools.base import Tool, ToolContext
from app.services.code.symbols import definition_pattern  # noqa: F401  (re-exported for callers and tests)
from app.services.repository.search import SearchResult

MAX_READ_LINES = 200


def _format_matches(result: SearchResult, empty_message: str) -> str:
    if not result.matches:
        return empty_message
    lines = [f"{m.path}:{m.line_number}: {m.line}" for m in result.matches]
    if result.truncated:
        lines.append(f"(stopped after {len(result.matches)} matches; narrow the search)")
    return "\n".join(lines)


class OverviewArgs(BaseModel):
    pass


class RepositoryOverviewTool(Tool[OverviewArgs]):
    name = "get_repository_overview"
    description = (
        "Get a summary of the repository: languages, frameworks, file count and top-level folders. "
        "Call this first when you do not know the project structure."
    )
    args_model = OverviewArgs

    def run(self, args: OverviewArgs, context: ToolContext) -> str:
        repository = context.repositories.get(context.repository_id)
        summary = repository.scan_summary or {}
        _, files = context.repositories.list_files(context.repository_id, limit=10_000)
        folders = Counter(f.path.split("/")[0] + "/" if "/" in f.path else "(root files)" for f in files)

        languages = ", ".join(f"{k} ({v})" for k, v in summary.get("languages", {}).items()) or "unknown"
        frameworks = ", ".join(summary.get("frameworks", [])) or "none detected"
        folder_lines = "\n".join(f"  {name}: {count} files" for name, count in folders.most_common(20))
        return (
            f"Repository: {repository.name}\n"
            f"Indexed files: {summary.get('file_count', len(files))}\n"
            f"Languages: {languages}\n"
            f"Frameworks: {frameworks}\n"
            f"Top-level folders:\n{folder_lines}"
        )


class ListFilesArgs(BaseModel):
    path_prefix: str | None = Field(default=None, description="Only files under this folder, e.g. 'app/services/'")
    language: str | None = Field(default=None, description="Only files of this language, e.g. 'Python'")
    limit: int = Field(default=100, ge=1, le=200)


class ListProjectFilesTool(Tool[ListFilesArgs]):
    name = "list_project_files"
    description = "List indexed source files with their language and line count. Filter by folder or language."
    args_model = ListFilesArgs

    def run(self, args: ListFilesArgs, context: ToolContext) -> str:
        total, files = context.repositories.list_files(
            context.repository_id, language=args.language, path_prefix=args.path_prefix, limit=args.limit
        )
        if not files:
            return "No files match these filters."
        lines = [f"{f.path} ({f.language or 'Other'}, {f.line_count} lines)" for f in files]
        header = f"Showing {len(files)} of {total} files:"
        return "\n".join([header, *lines])


class ReadFileArgs(BaseModel):
    path: str = Field(description="File path relative to the repository root, e.g. 'app/main.py'")
    start_line: int = Field(default=1, ge=1, description="First line to read (1-based)")
    end_line: int | None = Field(
        default=None, ge=1, description=f"Last line to read; at most {MAX_READ_LINES} lines are returned per call"
    )


class ReadFileTool(Tool[ReadFileArgs]):
    name = "read_file"
    description = (
        f"Read a source file with line numbers. Returns at most {MAX_READ_LINES} lines; "
        "use start_line/end_line to read other parts of long files."
    )
    args_model = ReadFileArgs

    def run(self, args: ReadFileArgs, context: ToolContext) -> str:
        last_allowed = args.start_line + MAX_READ_LINES - 1
        end_line = min(args.end_line, last_allowed) if args.end_line else last_allowed
        file = context.repositories.read_file(context.repository_id, args.path, args.start_line, end_line)

        width = len(str(file.end_line))
        numbered = [
            f"{number:>{width}} | {text}"
            for number, text in enumerate(file.content.splitlines(), start=file.start_line)
        ]
        header = f"File: {file.path} (lines {file.start_line}-{file.end_line} of {file.total_lines})"
        footer = []
        if file.end_line < file.total_lines:
            footer.append(
                f"... {file.total_lines - file.end_line} more lines. "
                f"Call read_file with start_line={file.end_line + 1} to continue."
            )
        return "\n".join([header, *numbered, *footer])


class SearchCodeArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200, description="Text to find (case-insensitive)")
    regex: bool = Field(default=False, description="Treat query as a Python regular expression")
    path_glob: str | None = Field(default=None, description="Only search matching paths, e.g. 'app/*.py'")
    max_results: int = Field(default=20, ge=1, le=50)


class SearchCodeTool(Tool[SearchCodeArgs]):
    name = "search_code"
    description = "Search all indexed files line by line. Returns 'path:line: text' for each match."
    args_model = SearchCodeArgs

    def run(self, args: SearchCodeArgs, context: ToolContext) -> str:
        result = context.repositories.search(
            context.repository_id,
            args.query,
            regex=args.regex,
            path_glob=args.path_glob,
            max_results=args.max_results,
        )
        # Models often search for a file name and then either conclude the file does not exist
        # (when no line mentions it) or read the first file that merely mentions it. So file-name
        # matches come first, and "no lines found" is not shown next to them.
        named: list[str] = []
        if not args.regex:
            _, files = context.repositories.list_files(context.repository_id, limit=100_000)
            needle = args.query.lower().replace("\\", "/")
            named = sorted(
                (f.path for f in files if needle in f.path.lower()),
                key=lambda p: (p.lower().rsplit("/", 1)[-1] != needle, p.count("/"), p),
            )[:10]
        if not named:
            return _format_matches(result, f"No matches for '{args.query}'.")

        output = (
            f"The file exists. Files whose path matches '{args.query}': " + ", ".join(named)
            + f"\nTo read it, call read_file with path='{named[0]}'."
        )
        if result.matches:
            output += "\n\nOther lines that mention it:\n" + _format_matches(result, "")
        return output


class FindFunctionArgs(BaseModel):
    name: str = Field(
        pattern=r"^[A-Za-z_$][A-Za-z0-9_$]*$",
        max_length=100,
        description="Exact function, method or class name, e.g. 'create_app'",
    )


class FindFunctionTool(Tool[FindFunctionArgs]):
    name = "find_function"
    description = (
        "Find where a function, method or class is defined (not where it is called). "
        "Works across common languages. Then use read_file to read it."
    )
    args_model = FindFunctionArgs

    def run(self, args: FindFunctionArgs, context: ToolContext) -> str:
        result = context.repositories.search_pattern(
            context.repository_id, definition_pattern(args.name), max_results=20
        )
        if not result.matches:
            return f"No definition of '{args.name}' found. Try search_code to find usages."
        first = result.matches[0]
        # Small models tend to stop after locating code; an explicit next step makes them read it.
        hint = (
            f"To see the implementation, call read_file with path='{first.path}' "
            f"and start_line={first.line_number}."
        )
        return f"{_format_matches(result, '')}\n{hint}"


class AnalyzeFileArgs(BaseModel):
    path: str = Field(description="File path relative to the repository root")


class AnalyzeFileTool(Tool[AnalyzeFileArgs]):
    name = "analyze_file"
    description = (
        "Static analysis of one file: its functions/classes with line ranges, the repository files it "
        "imports, and the files that import it. Use it to understand structure and relationships."
    )
    args_model = AnalyzeFileArgs

    def run(self, args: AnalyzeFileArgs, context: ToolContext) -> str:
        outline = context.insight.outline(context.repository_id, args.path)
        deps = context.insight.dependencies(context.repository_id, outline.path)

        lines = [f"File: {outline.path} ({outline.language or 'unknown language'})"]
        if not outline.supported:
            lines.append("Structure parsing is not supported for this language; use read_file.")
        elif outline.symbols:
            lines.append("Symbols:")
            lines += [f"  {s.kind} {s.qualified_name} (lines {s.start_line}-{s.end_line})" for s in outline.symbols[:80]]
        else:
            lines.append("No functions or classes found.")
        if outline.has_syntax_errors:
            lines.append("Warning: the parser found syntax errors in this file.")
        lines.append("Imports from repository: " + (", ".join(deps.imports) or "none"))
        lines.append("Imported by: " + (", ".join(deps.imported_by) or "none"))
        if deps.external:
            lines.append("External modules: " + ", ".join(deps.external[:20]))
        return "\n".join(lines)


def repository_tools() -> list[Tool]:
    return [
        RepositoryOverviewTool(),
        ListProjectFilesTool(),
        ReadFileTool(),
        SearchCodeTool(),
        FindFunctionTool(),
        AnalyzeFileTool(),
    ]
