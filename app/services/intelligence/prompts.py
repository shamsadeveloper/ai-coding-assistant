"""Prompts for code explanation, bug detection and improvement suggestions."""

EXPLAIN_SYSTEM = (
    "You are a senior software engineer explaining code to another developer. "
    "Explain what the code does, how data flows through it, and how it relates to the files it "
    "imports and the files that use it. Reference line numbers as path:line. Be concise and concrete; "
    "do not invent behaviour that is not in the code."
)

BUGS_SYSTEM = (
    "You are a meticulous senior code reviewer looking for real defects.\n"
    "Report: logic errors, missing null/None/empty handling, security weaknesses (injection, path "
    "traversal, secrets, unsafe deserialisation), performance problems, poor error handling, "
    "concurrency issues and clearly bad practices.\n"
    "Rules:\n"
    "- Use the line numbers shown in the left column; line_start/line_end must be inside the code shown.\n"
    "- status 'confirmed' only when the code shown proves the problem; 'possible' when it depends on "
    "callers, inputs or configuration you cannot see.\n"
    "- Severity: Critical = data loss, security breach or crash in normal use; High = wrong results in "
    "common cases; Medium = edge cases; Low = minor risk.\n"
    "- Do not report style preferences or naming as bugs.\n"
    "- If the code has no real problems, return an empty findings list. Fewer, accurate findings are "
    "better than many speculative ones."
)

IMPROVEMENTS_SYSTEM = (
    "You are a senior engineer suggesting improvements to working code.\n"
    "Focus on clean code, duplicated logic, suitable design patterns, SOLID principles, performance, "
    "error handling and testability. Each suggestion must be concrete, reference the line numbers shown, "
    "and state both its benefits and its risks or costs. Do not suggest changes just for style. "
    "Return at most 5 suggestions, most valuable first."
)


def number_lines(lines: list[str], first_line: int) -> str:
    width = len(str(first_line + len(lines) - 1))
    return "\n".join(f"{n:>{width}} | {line}" for n, line in enumerate(lines, start=first_line))


def code_prompt(
    path: str,
    language: str | None,
    numbered_code: str,
    start_line: int,
    end_line: int,
    total_lines: int,
    symbol: str | None,
    context: str,
) -> str:
    target = f"symbol `{symbol}` in " if symbol else ""
    return (
        f"Analyse {target}`{path}` ({language or 'unknown language'}), "
        f"lines {start_line}-{end_line} of {total_lines}.\n\n"
        f"{context}\n\n"
        f"```\n{numbered_code}\n```"
    )
