"""Masks credentials in text before it is sent to the model or stored from model output.

Files classified as sensitive (.env, keys, ...) are never read at all; this covers
secrets hard-coded inside ordinary source files.
"""

import re
from dataclasses import dataclass

REDACTED = "[REDACTED]"

_TOKEN_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),  # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),  # OpenAI/Anthropic-style API keys
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),  # Slack
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),  # Google API key
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWT
]

# password = "hunter2!"  /  "apiKey": "abc123..."  -> keep the name, hide the literal value.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (\b[\w.-]*(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|private[_-]?key)[\w.-]*\b
     ["']?\s*[:=]\s*)
    (["'])([^"'\s]{6,})\2
    """
)
# scheme://user:password@host -> scheme://user:[REDACTED]@host
_URL_CREDENTIALS = re.compile(r"\b([a-z][a-z0-9+.-]*://[^:/\s@]+:)([^@\s/]+)(@)", re.I)


@dataclass(frozen=True)
class Redaction:
    text: str
    count: int


def redact(text: str) -> Redaction:
    count = 0

    def token(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        # Keep the line count so line numbers in findings and diffs stay correct.
        return REDACTED + "\n" * match.group(0).count("\n")

    def assignment(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}{match.group(2)}{REDACTED}{match.group(2)}"

    def url(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}{REDACTED}{match.group(3)}"

    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(token, text)
    text = _ASSIGNMENT.sub(assignment, text)
    text = _URL_CREDENTIALS.sub(url, text)
    return Redaction(text, count)


def redact_text(text: str) -> str:
    return redact(text).text
