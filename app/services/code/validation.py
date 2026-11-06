"""Syntax validation for generated or modified source code."""

import ast

from app.services.code.syntax import parse_outline
from app.services.repository.language import detect_language


def syntax_error(path: str, content: str, language: str | None = None) -> str | None:
    """A description of the first syntax error, or None if the file parses (or cannot be checked)."""
    if path.endswith(".py"):
        try:
            ast.parse(content)
        except SyntaxError as exc:
            return f"SyntaxError on line {exc.lineno}: {exc.msg}"
        return None
    outline = parse_outline(path, language or detect_language(path), content)
    return "The parser found syntax errors" if outline.supported and outline.has_syntax_errors else None
