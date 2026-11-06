"""Prompts for the fix-session stages."""

INVESTIGATE_SYSTEM = (
    "You are a senior engineer investigating a reported bug. Find the ROOT CAUSE: it may be in a "
    "different function or file than where the symptom appears.\n"
    "The code around the bug, its callers, imports and related tests are given below. Use the tools only "
    "to check what is not shown (a called function, a caller, a config value). Do not write a patch.\n"
    "When done, answer in plain text with: the root cause as path:line, the evidence (quote the exact "
    "lines with path:line), which files must change, and the smallest fix. If the code is actually "
    "correct, say clearly that it is not a bug."
)

EXTRACT_SYSTEM = (
    "Convert the bug investigation into the requested JSON.\n"
    "- evidence: 1 to 4 items. Each quote is ONE line of code copied exactly from the code shown, with "
    "its real line number. Do not invent code.\n"
    "- Use only file paths that appear in the context.\n"
    "- affected_files: only the files that must be edited (usually one).\n"
    "- is_real_bug=false when the investigation found the code correct."
)

FIX_REQUEST = (
    "Fix this bug with the smallest possible change. Change only what the root cause requires; keep the "
    "existing behaviour, signatures and style everywhere else. Do not edit or delete tests.\n\n"
    "Bug: {description}\n"
    "Root cause: {root_cause}\n"
    "Evidence:\n{evidence}\n"
    "Fix strategy: {strategy}"
)

RETRY_FEEDBACK = (
    "\n\nA previous attempt did NOT work. Do not repeat it.\n"
    "Previous change:\n{diff}\n"
    "Why it failed:\n{reason}"
)

FAILURE_ANALYSIS_SYSTEM = (
    "A code change was tested and some tests failed that passed before. In at most 5 short sentences: "
    "explain why the change broke them and what the next attempt must do differently. Refer to the "
    "test names and path:line where the output shows them. Do not write code."
)

REVIEW_SYSTEM = (
    "You review a bug fix before a developer applies it. Check: does the change fix the stated root "
    "cause; could it break callers or other behaviour (regressions); does it introduce security problems "
    "(injection, unsafe input handling, leaked secrets); is it minimal. Report only real issues; an empty "
    "issues list with verdict 'approve' is fine for a correct fix."
)

REPRODUCTION_SYSTEM = (
    "Write ONE pytest test function that reproduces the described bug: it must FAIL with the current "
    "code and PASS once the bug is fixed. Import the code under test the way the existing tests do. "
    "Reply with one fenced ```python code block containing the complete test file and nothing else."
)
